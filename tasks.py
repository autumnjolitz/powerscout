import inspect
import json
import sys
import os
import functools
import types
import shutil
import hashlib
import tempfile
import dataclasses
import pprint
import typing
import zipfile
import tarfile
import importlib
import builtins
from collections.abc import Iterable
from typing import Any, Literal

from pathlib import Path
from invoke import task as _task
from invoke.context import Context
from contextlib import suppress, closing, contextmanager
from typing import TypeVar

try:
    from typing import get_overloads, overload
except ImportError:
    from typing_extensions import get_overloads, overload

_ = types.SimpleNamespace()
this = sys.modules[__name__]


def is_context_param(
    param: inspect.Parameter, context_param_names: tuple[str, ...] = ("c", "ctx", "context")
) -> None | Literal["name", "type", "name_and_type"]:
    value = None
    if param.name in context_param_names:
        value = "name"
    if param.annotation:
        if param.annotation is Context:
            if value:
                value = f"{value}_and_type"
            else:
                value = "type"
        elif typing.get_origin(param.annotation) is typing.Union:
            if Context in typing.get_args(param.annotation):
                if value:
                    value = f"{value}_and_type"
                else:
                    value = "type"
    return value


@dataclasses.dataclass(frozen=True, order=True, slots=True)
class FoundType:
    in_namespace: bool = dataclasses.field(hash=True, compare=True)
    namespace_path: tuple[str, ...] = dataclasses.field(hash=True, compare=True)
    namespace_values: tuple[Any, ...] = dataclasses.field(hash=False, compare=False)

    @property
    def key(self):
        return self.namespace_path[0]

    @property
    def value(self):
        return self.namespace_values[0]


def is_literal(item) -> bool:
    with suppress(AttributeError):
        return (item.__module__, item.__name__) in (
            ("typing", "Literal"),
            ("typing_extensions", "Literal"),
        )
    return False


def is_type_container(item):
    origin = typing.get_origin(item)
    if origin is None:
        return False
    return True


def get_types_from(
    annotation,
    in_namespace: dict[str, Any] | None = None,
) -> Iterable[FoundType]:
    if in_namespace is None:
        in_namespace = vars(this)
    if annotation is inspect.Signature.empty:
        annotation = Any
    if isinstance(annotation, str):
        ns = {}
        exec(f"annotation = {annotation!s}", vars(this), ns)
        annotation = ns["annotation"]

    if is_literal(annotation):
        return
    if annotation is Any:
        return
    type_name = None
    with suppress(AttributeError):
        type_name = annotation.__qualname__
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is not None and args is not None:
        for module in types, typing, builtins:
            for value in vars(module).values():
                if value is origin:
                    for arg in args:
                        yield from get_types_from(arg, in_namespace)
                    return
        else:
            raise NotImplementedError(f"Unsupported origin type {origin!r} {annotation}")
        assert not args and not origin

    assert isinstance(
        annotation, type
    ), f"not a type - {annotation!r} {type(annotation)} {annotation.__module__}"
    if type_name.split(".")[0] in vars(builtins):
        return
    if f"{annotation.__module__}.{annotation.__name__}" != annotation.__qualname__:
        type_name = f"{annotation.__module__}.{annotation.__name__}"
    path = []
    target = types.SimpleNamespace(**in_namespace)
    path_values = []
    for step in type_name.split("."):
        path.append(step)
        try:
            target = getattr(target, step)
        except AttributeError as e:
            try:
                target = getattr(this, path[0])
            except AttributeError:
                try:
                    # print('trying', path, type_name)
                    target = importlib.import_module(".".join(path))
                except ImportError:
                    raise e from None
        path_values.append(target)

    yield FoundType(path[0] in in_namespace, path, path_values)


def reify_annotations_in(
    namespace: dict[str, Any], signature: inspect.Signature
) -> inspect.Signature:
    for index, param in enumerate(signature.parameters):
        param = signature.parameters[param]
        for result in get_types_from(param.annotation, namespace):
            if result.in_namespace:
                continue
            namespace[result.key] = result.value
            # print('setting', result.key, 'to', result.value)
    for result in get_types_from(signature.return_annotation):
        if result.in_namespace:
            continue
        namespace[result.key] = result.value
    return signature


def sanitize_return(func, ns):
    NOT_SET = object()
    sig = inspect.signature(func)
    if sig.return_annotation is inspect.Signature.empty:
        returns = NOT_SET
        for overload_func in get_overloads(func):
            overload_signature = reify_annotations_in(ns, inspect.signature(overload_func))
            print(overload_signature)
            if returns is NOT_SET:
                returns = overload_signature.return_annotation
                continue
            returns |= overload_signature.return_annotation
        if returns is not NOT_SET:
            sig = sig.replace(return_annotation=returns)
        else:
            sig = sig.replace(return_annotation=Any)
    return sig


def task(func):
    blank = ""
    this = sys.modules[__name__]
    ns = {"this": this, "typing": typing, "pprint": pprint, "json": json, "Union": typing.Union}
    sig = sanitize_return(func, ns)
    inner_function_call = sig
    is_contextable = False

    if sig.parameters:
        for param in sig.parameters:
            if is_context_param(sig.parameters[param]):
                is_contextable = True
            break
    if not is_contextable:
        for index, param in enumerate(sig.parameters):
            param = sig.parameters[param]
            if not index:
                continue
            if is_context_param(param) in ("type", "name_and_type"):
                # okay, the context is definitely out of order
                raise NotImplementedError(
                    "TODO: Implement generating an inner_function_call with rearranged values"
                )

    if is_contextable:

        @functools.wraps(func)
        def unwrapped(*args, **kwargs):
            return func(Context(), *args, **kwargs)

        setattr(globals()["_"], func.__name__, unwrapped)
        if "return" not in func.__annotations__:
            func.__annotations__["return"] = sig.return_annotation
        func.__doc__ = f"{func.__doc__ or blank}\n:returns: {safe_annotation_string_from(sig.return_annotation)}"
        return _task(func)

    sig_funccall = []
    for param_name in inner_function_call.parameters:
        param = inner_function_call.parameters[param_name]
        if param.kind in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        ):
            sig_funccall.append(f"{param.name}")
        elif param.kind is inspect.Parameter.KEYWORD_ONLY:
            sig_funccall.append(f"{param.name}={param.name}")
        elif param.kind is inspect.Parameter.VAR_KEYWORD:
            sig_funccall.append(f"**{param.name}")
        elif param.kind is inspect.Parameter.VAR_POSITIONAL:
            sig_funccall.append(f"*{param.name}")

    new_signature = reify_annotations_in(
        ns,
        sig.replace(
            parameters=(
                inspect.Parameter("context", inspect.Parameter.POSITIONAL_ONLY, annotation=Context),
                *sig.parameters.values(),
            )
        ),
    )

    code = """
def {name}{args}:
    value = this._.{name}({sig_funccall})
    try:
        print(json.dumps(value, indent=4, sort_keys=True), file=sys.stderr)
    except ValueError:
        pprint.pprint(value, stream=sys.stderr)
    return value
""".format(
        name=func.__name__, args=str(new_signature), sig_funccall=", ".join(sig_funccall)
    )
    # print(code, ns)
    exec(code, vars(this), ns)
    setattr(globals()["_"], func.__name__, func)
    wrapped_func = ns[func.__name__]
    wrapped_func.__doc__ = f"{func.__doc__ or blank}\n:returns: {safe_annotation_string_from(new_signature.return_annotation)}"
    return _task(wrapped_func)


def safe_annotation_string_from(annotation):
    if str(annotation).startswith("<class "):
        annotation = annotation.__name__
    return annotation


@task
def project_root(type: type[str] | type[Path] | Literal["str", "Path"] = "str") -> str | Path:
    """
    Get the absolute path of the project root assuming tasks.py is in the repo root.
    """
    if isinstance(type, builtins.type):
        type = type.__name__
    assert type in ("str", "Path"), f"{type} may be str or Path"
    root = Path(__file__).resolve().parent
    if type == "str":
        return str(root)
    return root


@task
def python_path(
    type_name: Literal["str", "Path", str, Path] = "str", *, skip_venv: bool = False
) -> str | Path:
    """
    Return the best python to use
    """
    if isinstance(type_name, type):
        type_name = type_name.__name__
    assert type_name in ("Path", "str")
    root = Path(__file__).resolve().parent
    python = root / "python" / "bin" / "python"
    if not python.exists():
        with suppress(KeyError):
            python = Path(os.environ["VIRTUAL_ENV"]) / "bin" / "python"
    if skip_venv or not python.exists():
        python = Path(
            shutil.which("python3"),
            path=":".join(x for x in os.environ["PATH"].split(":") if Path(x) != python.parent),
        ).resolve(True)
    if type_name == "str":
        return str(python)
    return python


@task
def hashed_package_manifest(filename: str) -> dict[str, bytes]:
    if not isinstance(filename, Path):
        filename = Path(filename)
    filename = filename.resolve(True)
    manifest = {}
    if filename.name.endswith(".tar.gz"):
        with tarfile.open(filename, mode="r:*") as fh:
            for entry in fh:
                stream = fh.extractfile(entry)
                if stream is None:
                    continue
                with closing(stream):
                    hasher = hashlib.sha1()
                    for blob in iter(functools.partial(stream.read, 4096 * 4), b""):
                        hasher.update(blob)
                    manifest[entry.name] = hasher.digest()
        return manifest
    raise NotImplementedError


@task
def diff(
    left: Path | str | dict[str, Any], right: Path | str | dict[str, Any]
) -> dict[str, tuple[str, Any] | tuple[Any, Any]]:
    if not isinstance(left, dict):
        with open(left) as fh:
            left = json.load(fh)
    if not isinstance(right, dict):
        with open(right) as fh:
            right = json.load(fh)
    deltas = []
    for key in left.keys() & right.keys():
        if left[key] != right[key]:
            delta = (left[key], right[key])
            deltas.append((key, delta))
    for key in left.keys() - right.keys():
        deltas.append((f"-{key}", left[key]))
    for key in right.keys() - left.keys():
        deltas.append((f"+{key}", right[key]))
    return dict(deltas)


def sliding_window(s: Iterable[Any], mode: Literal["before", "after"], /):
    assert mode in ("before", "after")
    if mode == "before":
        last_value = None
        for item in s:
            yield last_value, item
            last_value = item
        return
    gen = iter(s)
    value = next(gen)
    with suppress(StopIteration):
        try:
            future_value = next(gen)
        except StopIteration:
            yield value, None
        else:
            while True:
                yield value, future_value
                value = future_value
                future_value = next(gen)


class PackageVersion(typing.NamedTuple):
    name: str
    version: str


@task
def version_from(package_archive: str | Path) -> PackageVersion:
    """
    Given a wheel or sdist archive, return the name and version
    """
    if hasattr(package_archive, "resolve"):
        package_archive = str(package_archive.resolve(True))
    name = version = None
    with package_metadata_stream_from(package_archive) as pkg_info:
        for line in pkg_info:
            if line.startswith(b"Version: ") and version is None:
                version = line[len(b"Version: ") :].strip().decode()
            elif line.startswith(b"Name: ") and name is None:
                name = line[len(b"Name: ") :].strip().decode()
    return PackageVersion(name, version)


@task
def metadata_from(package_archive: str) -> dict[str, str]:
    metadata = {}
    with package_metadata_stream_from(package_archive) as fh:
        for previous, line in sliding_window(fh, "before"):
            if line == b"\n":
                break
            key, value = line.strip().split(b":", 1)
            metadata[key] = value
        metadata[b"Description"] = b"".join(fh)
    return {key.decode(): value.decode() for key, value in metadata.items()}


@contextmanager
def package_metadata_stream_from(package_archive: str) -> tuple[str, str]:
    name = version = None
    if package_archive.endswith(".tar.gz"):
        with tarfile.open(package_archive) as fh:
            for member in fh:
                if member.name.endswith("PKG-INFO"):
                    with fh.extractfile(member) as pkg_info:
                        yield pkg_info
                        break
    elif package_archive.endswith((".zip", ".whl")):
        with zipfile.ZipFile(package_archive, "r") as fh:
            for member in fh.infolist():
                if member.filename.endswith("METADATA"):
                    with closing(fh.open(member)) as pkg_info:
                        yield pkg_info
                        break


@task
def build_packages(context: Context, force: bool = False) -> None | tuple[Path, ...]:
    dist = Path("dist")
    new_assets = None
    with suppress(FileNotFoundError):
        dist = dist.resolve(True)
        if not dist.is_dir():
            os.remove(dist)
    if not dist.exists():
        dist.mkdir()
    if force:
        for file in dist.iterdir():
            if file.is_file():
                os.remove(file)

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        context.run(f"{_.python_path()} setup.py sdist -d {tmpdir!s}", hide="both")
        (new_sdist,) = Path(tmpdir).resolve(True).iterdir()
        differences = True
        with suppress(FileNotFoundError):
            existing_hash = _.hashed_package_manifest(dist / new_sdist.name)
            new_hash = _.hashed_package_manifest(new_sdist)
            differences = _.diff(existing_hash, new_hash)
        if differences:
            with suppress(FileNotFoundError):
                os.remove(dist / new_sdist.name)
            context.run(f"{_.python_path()} setup.py bdist_wheel -d {tmpdir!s}", hide="both")
            for file in tmpdir.iterdir():
                with suppress(FileNotFoundError):
                    if file.samefile(dist / file.name):
                        continue
                print(file, "->", dist / file.name)
                shutil.move(file, dist / file.name)
                new_assets.append(dist / file.name)
        else:
            name, version = _.version_from(new_sdist)
            print(f"Current sdist at {dist / new_sdist.name} is unchanged", file=sys.stderr)
            for file in dist.iterdir():
                if file.name.endswith(".whl"):
                    existing_wheel_package, existing_wheel_version = _.version_from(file)
                    if (existing_wheel_package, existing_wheel_version) == (name, version):
                        print(f"Current wheel at { file } is unchanged", file=sys.stderr)
                        break
            else:
                with tempfile.TemporaryDirectory() as tmpdir:
                    context.run(
                        f"{_.python_path()} setup.py bdist_wheel -d {tmpdir!s}", hide="both"
                    )
                    (new_wheel,) = Path(tmpdir).iterdir()
                    print(new_wheel, "->", dist / new_wheel.name)
                    shutil.move(new_wheel, dist / new_wheel.name)
                    new_assets.append(dist / new_wheel.name)
    return tuple(new_assets) or None


@task
def setup(context, python_bin: str | None = None):
    if python_bin is None:
        python_bin = _.python_path(Path, skip_venv=True)
    venv = root / "python"
    if python_bin == venv:
        shutil.rmtree(venv)
    context.run(f"{python_bin} -m venv {venv!s}")


@task
def build(context):
    build_packages(context)
    context.run(
        "docker compose " "-f config/docker-compose.yml " "build",
        env={"BUILDKIT_PROGRESS": "plain"},
    )


@task
def run(context, mode="local"):
    if mode == "local":
        context.run(f"{_.python_path()} -m powerscout -d cli")
    elif mode == "docker":
        context.run(
            "docker compose -f config/docker-compose.yml up --exit-code-from api --force-recreate "
        )
