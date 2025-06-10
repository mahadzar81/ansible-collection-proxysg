import os
import sys
import inspect
import time
import traceback
import unittest
import argparse
import configparser
from types import MethodType
from xml.dom.minidom import Document
from typing import Optional, Dict, Any, List, Tuple, Union

__author__ = "Maza"
__version__ = "1.1"

_gLogFilter: List[str] = []
_gLogPrefix: Dict[str, Tuple[str, bool]] = {
    'pass': ('++', False),
    'fail': ('$$', True),
    'error': ('**', True),
    'fatal': ('XX', True)
}
_gTestClassName: str = ''
aspectsDefault = {'autotest.termination.seconds': 600}
aspects: Dict[str, Any] = {}

# -- Find paths
curPath = os.path.dirname(os.path.abspath(inspect.stack()[1][1]))
dataPath = [curPath]
toolPath = [curPath]

while True:
    libpath = os.path.join(curPath, 'lib')
    if os.path.isdir(libpath) and libpath not in sys.path:
        sys.path.append(libpath)
    dpath = os.path.join(curPath, 'data')
    if os.path.isdir(dpath):
        dataPath.append(dpath)
    tpath = os.path.join(curPath, 'tools')
    if os.path.isdir(tpath):
        toolPath.append(tpath)
    newpath = os.path.dirname(curPath)
    if newpath == curPath:
        break
    curPath = newpath


class Error(Exception):
    pass


class DotDict(dict):
    """Access dictionary keys via dot notation."""
    def __init__(self, value=None):
        super().__init__()
        if isinstance(value, dict):
            for key, val in value.items():
                self[key] = DotDict(val) if isinstance(val, dict) else val
        elif value is not None:
            raise TypeError("Expected dictionary")

    def __setitem__(self, key, value):
        if '.' in key:
            key1, key_rest = key.split('.', 1)
            if key1 not in self or not isinstance(self[key1], DotDict):
                self[key1] = DotDict()
            self[key1][key_rest] = value
        else:
            if isinstance(value, dict) and not isinstance(value, DotDict):
                value = DotDict(value)
            super().__setitem__(key, value)

    def __getitem__(self, key):
        if '.' in key:
            key1, key_rest = key.split('.', 1)
            return self[key1][key_rest]
        return super().__getitem__(key)

    def __contains__(self, key):
        if '.' in key:
            key1, key_rest = key.split('.', 1)
            return key1 in self and key_rest in self[key1]
        return super().__contains__(key)

    def setdefault(self, key, default):
        if key not in self:
            self[key] = default
        return self[key]


aspects = DotDict(aspectsDefault)


def aspect(name: str) -> Any:
    return get_aspect(name)


def get_aspect(name: str) -> Any:
    try:
        return aspects[name]
    except KeyError:
        raise Error(f"** Aspect not available: {name}")


def log_filter(filters: List[str]):
    global _gLogFilter
    _gLogFilter = filters


def log(filter_type: str, text: str = '', index: str = ''):
    prefix, always_show = _gLogPrefix.get(filter_type, ('..', False))
    if filter_type in _gLogFilter or always_show:
        print(f"{prefix} {filter_type}{index}: {text}")


def log_code(text: str = ''):
    frame = sys._getframe(1)
    file = frame.f_code.co_filename
    func = frame.f_code.co_name
    line = frame.f_lineno
    log('code', f'{func} line: {line} {text}')


def log_exception(filter_type: str, additional_info: str = ''):
    exc_info = sys.exc_info()
    error_name = f"{exc_info[1].__module__}.{exc_info[0].__name__}"
    error_value = exc_info[1].args
    trace = traceback.format_tb(exc_info[2])
    if additional_info:
        additional_info = f'== {additional_info}\n'
    log(filter_type, f"{additional_info}== EXCEPTION: {error_name}\n== {error_value}\n{''.join(trace)}\n========")


def data_filepath(filename: str, assert_on_error: bool = True) -> Optional[str]:
    for path in dataPath:
        fp = os.path.join(path, filename)
        if os.path.isfile(fp):
            return fp
    if assert_on_error:
        raise Error(f'Data file "{filename}" not found in: {", ".join(dataPath)}')
    return None


def tool_filepath(filename: str, assert_on_error: bool = True) -> Optional[str]:
    for path in toolPath:
        fp = os.path.join(path, filename)
        if os.path.isfile(fp):
            return fp
    if assert_on_error:
        raise Error(f'Tool file "{filename}" not found in: {", ".join(toolPath)}')
    return None


def termination_handler(signum, frame):
    raise Error("Termination timer expired")


def enhance_method(base_class, method_name: str, replacement):
    orig_method = getattr(base_class, method_name)

    def enhanced(self, *args, **kwargs):
        return replacement(orig_method, self, *args, **kwargs)

    setattr(base_class, method_name, MethodType(enhanced, base_class))


def method_wrapper(method, self, *args, **kwargs):
    method(self, *args, **kwargs)
    msg = kwargs.get('msg', args[-1])
    log('pass', msg)


class TestFrame(unittest.TestCase):
    def __init__(self, methodName='runTest'):
        super().__init__(methodName)
        for name in dir(unittest.TestCase):
            if name.startswith('assert') and not name.startswith('assert_'):
                enhance_method(TestFrame, name, method_wrapper)


class AutoTestResult(unittest.TextTestResult):
    separator1 = '=' * 70
    separator2 = '-' * 70

    def __init__(self, stream, descriptions, verbosity):
        super().__init__(stream, descriptions, verbosity)
        self.run_times = []

    def startTest(self, test):
        log('info', f'==== {test._testMethodName} ====')
        self._start_time = time.time()
        super().startTest(test)

    def stopTest(self, test):
        self.run_times.append((test, time.time() - self._start_time))
        super().stopTest(test)

    def addSuccess(self, test):
        super().addSuccess(test)
        log('pass', 'success')

    def addError(self, test, err):
        super().addError(test, err)
        log('error', str(err[1]))

    def addFailure(self, test, err):
        super().addFailure(test, err)
        log('fail', str(err[1]))


class TestRunner(unittest.TextTestRunner):
    def __init__(self, stream=sys.stderr, descriptions=True, verbosity=1):
        super().__init__(stream=stream, descriptions=descriptions, verbosity=verbosity)

    def run(self, suite):
        result = AutoTestResult(self.stream, self.descriptions, self.verbosity)
        suite(result)
        return result


def parse_config_file(filename: str):
    global aspects
    if not os.path.isfile(filename):
        raise Error(f'Configuration file does not exist: {filename}')
    config = configparser.ConfigParser()
    config.read(filename)
    aspects = DotDict(aspectsDefault)
    try:
        for k, v in config.items('aspects'):
            aspects[k] = v
    except configparser.NoSectionError:
        raise Error('Configuration file must contain "[aspects]" section tag')


def run(a_module):
    parser = argparse.ArgumentParser(description="Run tests")
    parser.add_argument('--config', required=True, help='Configuration file')
    parser.add_argument('--log', help='Comma-separated list of log filters')
    parser.add_argument('--params', help='Comma-separated parameters')
    parser.add_argument('--failfast', action='store_true', help='Stop on first failure')
    parser.add_argument('testname', nargs='*', help='Name of test(s)')
    args = parser.parse_args()

    global _gTestClassName
    _gTestClassName = a_module.__name__
    fn = os.path.basename(sys.argv[0]).split('.')[0]
    if fn != a_module.__name__:
        print(f'** WARNING: filename "{fn}" should match test class name: "{a_module.__name__}"')

    if args.log:
        log_filter(args.log.split(','))
    if not args.config:
        raise Error('configuration file required')

    parse_config_file(args.config)
    if args.testname:
        suite = unittest.TestSuite(map(lambda n: a_module(n), args.testname))
    else:
        suite = unittest.defaultTestLoader.loadTestsFromTestCase(a_module)

    runner = TestRunner(stream=sys.stdout, verbosity=2 if args.failfast else 1)
    runner.run(suite)


def run_auto(a_module, test_names: List[str], test_aspects: Dict[str, Any], log_options: List[str]):
    global _gTestClassName, aspects
    _gTestClassName = a_module.__name__
    log_filter(log_options)
    aspects = DotDict(aspectsDefault)
    for k, v in test_aspects.items():
        aspects[k] = v
    suite = unittest.TestSuite(map(lambda n: a_module(n), test_names))
    return TestRunner(stream=sys.stdout).run(suite)