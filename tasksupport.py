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
        name=func.__name__, args=str(new_signature), sig_funccall="".join(sig_funccall)
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
