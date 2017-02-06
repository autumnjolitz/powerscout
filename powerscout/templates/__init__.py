import pkg_resources
import jinja2
env = jinja2.Environment()
env.globals['len'] = len
env.globals['sorted'] = sorted


def load_template(filename):
    return env.from_string(pkg_resources.resource_string(__name__, filename).decode('utf8'))


TEMPLATES = {
    filename: load_template(filename)
    for filename in pkg_resources.resource_listdir(__name__, '')
    if filename.endswith('.html')
}


def render_template(name, **kwargs):
    return TEMPLATES[name].render(**kwargs)
