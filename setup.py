import sys
from codecs import open  # To use a consistent encoding
from os import path

# Always prefer setuptools over distutils
from setuptools import (setup, find_packages)

if sys.version_info[0] == 2:
    sys.stderr.write("This package only supports Python 3+.\n")
    sys.exit(1)

here = path.abspath(path.dirname(__file__))
install_requirements = [
    'sanic~=0.6.0',
    'sanic-jinja2~=0.5.2',
    'redis',
    'beautifulsoup4',
    'PyYAML',
    'lxml',
    'requests'
]

# The following are meant to avoid accidental upload/registration of this
# package in the Python Package Index (PyPi)
pypi_operations = frozenset(['register', 'upload']) & frozenset([x.lower() for x in sys.argv])
if pypi_operations:
    raise ValueError('Command(s) {} disabled in this example.'.format(', '.join(pypi_operations)))

# Python favors using README.rst files (as opposed to README.md files)
# If you wish to use README.md, you must add the following line to your MANIFEST.in file::
#
#     include README.md
#
# then you can change the README.rst to README.md below.
with open(path.join(here, 'README.rst'), encoding='utf-8') as fh:
    long_description = fh.read()

# We separate the version into a separate file so we can let people
# import everything in their __init__.py without causing ImportError.
__version__ = None
exec(open('powerscout/about.py').read())
if __version__ is None:
    raise IOError('about.py in project lacks __version__!')

setup(name='powerscout', version=__version__,
      author='Ben Jolitz',
      description='Server for Rainforest EAGLE -> Graphite',
      long_description=long_description,
      license='BSD',
      packages=find_packages(exclude=['contrib', 'docs', 'tests*']),
      include_package_data=True,
      install_requires=install_requirements,
      keywords=['rainforest', 'eagle', 'API', 'graphite'],
      url="https://github.com/benjolitz/powerscout",
      classifiers=[
        "Development Status :: 3 - Alpha",
        "Topic :: Utilities",
        "License :: OSI Approved :: BSD License",
      ])
