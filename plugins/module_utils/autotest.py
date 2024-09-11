#! /usr/local/bin/python

"""Main module to run manual and automated testing scripts

==== usage ====

Name of file: sampletest.py
The filename must match test class (with .py attached)
Inside the test class are two routines "setUp" and "tearDown" which are run before and after each test
The test class contains one or more test routines
NOTE: test routines ALL start with "test_" this distinguishes them from other routines in the class

Tests contain "asserts" which determine pass or fail of checks
Asserts have two or three paramters, the first one (or two) is for comparison
In this case the first paramter must eveluate to True for a pass
If it evaluates to False then an excpetion is thrown and the test stops
The last paramter is a statement on what this test is.
It should be a POSSITIVE statement of what passing is
More on asserts in documentation
		

import autotest


# -- Test class

class sampletest (autotest.TestFrame):
	'''Short description of tests in this file - ie.
	A series of sample tests'''

	# --------------------------------------------------------------------------------
	
	def setUp (self):
		'''Stuff to initiallize before a test runs - ie.
		Set a class value'''
		
		self.value = 4

	# --------------------------------------------------------------------------------
	
	def tearDown (self):
		'''Stuff to shutdown after a test runs - ie.
		Clear class value'''
		
		self.value = None

	# --------------------------------------------------------------------------------
	
	def test_isfour (self):
		'''Description of this test - ie.
		Test if the class value is four'''
		
		self.assertTrue (self.value == 4, 'value is four')
		
	# --------------------------------------------------------------------------------
	
	def test_isfive (self):
		'''Description of this tests - ie.
		Verify class value is 5 (this will fail due to preset to 4)'''		
		
		self.assertTrue (self.value == 5, 'value is five')

# ====================================================================================================
# -- Code to run this test manually, note that the parameter is same as test class and filename

if __name__ == '__main__':
	autotest.run (sampletest)


==== Running a test as standalone ====

./sampletest.py test_isfour -c <configuration_file> -l <logging_options>

See documentation for format fo configuration_file and logging_options
"""

__author__ = "Maza"
__version__ = "1.0"


import sys, os, re
import os.path
import signal
import time
import traceback
import unittest
import optparse
import ConfigParser
import inspect

# -- Filter array set by command line options
# -- prefix text and flag to always show text

_gLogFilter = []
_gLogPrefix = {'pass':('++',False), 'fail':('$$',True), 'error':('**',True), 'fatal':('XX',True)}
_gTestClassName = ''

# -- Configuration variables - see documentation for format and use
aspectsDefault = {'autotest.termination.seconds':600}
aspects = {}

# -- Get directory path of test file from a stack inspection
# -- Find lib/tool/data directories and add to global path variables
curPath = os.path.dirname(os.path.abspath(inspect.stack()[1][1]))
dataPath = [curPath]
toolPath = [curPath]
while 1:
	libpath = os.path.join(curPath,'lib')
	if os.path.isdir(libpath) and libpath not in sys.path: sys.path.append(libpath)
	dpath = os.path.join(curPath,'data')
	if os.path.isdir(dpath): dataPath.append(dpath)
	tpath = os.path.join(curPath,'tools')
	if os.path.isdir(tpath): toolPath.append(tpath)
	newpath = os.path.dirname(curPath)
	if newpath == curPath: break
	curPath = newpath

# ------------------------------------------------------------------------------

class Error (Exception): pass		# local error

# ------------------------------------------------------------------------------

class dotdictify(dict):
    '''Change text doted notation to nested class/dictionary notation'''
	
    def __init__(self, value=None):
        if value is None:
            pass
        elif isinstance(value, dict):
            for key in value:
                self.__setitem__(key, value[key])
        else:
            raise TypeError, 'expected dictionary'

    def __setitem__(self, key, value):
        if '.' in key:
            myKey, restOfKey = key.split('.', 1)
            target = self.setdefault(myKey, dotdictify())
            if not isinstance(target, dotdictify):
                raise KeyError, 'cannot set "%s" in "%s" (%s)' % (restOfKey, myKey, repr(target))
            target[restOfKey] = value
        else:
            if isinstance(value, dict) and not isinstance(value, dotdictify):
                value = dotdictify(value)
            dict.__setitem__(self, key, value)

    def __getitem__(self, key):
#    	if key == '__getstate__': return self.__getstate__
        if '.' not in key:
            return dict.__getitem__(self, key)
        myKey, restOfKey = key.split('.', 1)
        target = dict.__getitem__(self, myKey)
        if not isinstance(target, dotdictify):
            raise KeyError, 'cannot get "%s" in "%s" (%s)' % (restOfKey, myKey, repr(target))
        return target[restOfKey]

    def __contains__(self, key):
        if '.' not in key:
            return dict.__contains__(self, key)
        myKey, restOfKey = key.split('.', 1)
        target = dict.__getitem__(self, myKey)
        if not isinstance(target, dotdictify):
            return False
        return restOfKey in target

	def __getstate__(self):
		return self.__dict__.items()
	def __setstate__(self):
		pass

    def setdefault(self, key, default):
        if key not in self:
            self[key] = default
        return self[key]

    __setattr__ = __setitem__
    __getattr__ = __getitem__
    
aspects = dotdictify(aspectsDefault)

# ------------------------------------------------------------------------------
def aspect (name):
	return getAspect (name)
	
def getAspect (name):
	'''Return contents of an Aspect'''
	
	global aspects
	try:
		return aspects[name]
	except:
		raise Error ('** Aspect not available: {0}'.format(name))

# ------------------------------------------------------------------------------

def logFilter (filter):
	'''Set loging output filter, array of filter names'''
	
	global _gLogFilter
	_gLogFilter = filter
	
# ------------------------------------------------------------------------------

def log (filter, text='', index=''):
	'''Display text if filter exists'''
	
	global _gLogFilter, _gLogPrefix
	if not text: text=''
	prefix, alwaysShow = _gLogPrefix.get (filter,('..',False))	
	if filter in _gLogFilter or alwaysShow:
		sys.stdout.write ('{0} {1}{2}: {3}\n'.format (prefix, filter, index, text))


# ------------------------------------------------------------------------------

def logCode (text=''):
	'''Display code name and line number'''
	
	global _gLogFilter, _gLogPrefex

	file = sys._getframe(1).f_code.co_filename
	func = sys._getframe(1).f_code.co_name
	line = sys._getframe(1).f_lineno
	log ('code', '{0} line: {1} {2}'.format(func,line, text))

# ------------------------------------------------------------------------------

def logException (filter, additionalInfo=''):
	'''Report stack on exception'''

	x = sys.exc_info()
	excErrorName = "{0}.{1}".format (x[1].__module__, x[0].__name__)
	excErrorValue = x[1].args
	trace = traceback.format_tb (x[2])
	if additionalInfo:
		additionalInfo = '== {0}\n'.format(additionalInfo)
	
	log (filter, "{0}== EXCEPTION: {1}\n== {2}\n{3}\n========\n".format (additionalInfo, excErrorName, excErrorValue, ''.join(trace)))

# ------------------------------------------------------------------------------

def dataFilepath (filename, assertOnError=True):
	'''
	Finds a file within the local data directory tree
	filename - name of file
	assertOnError - True/False, throw error when file not found
	Returns:
		1. full filepath of found file
		2. None if file not found and assertOnError is False
	'''
	global dataPath
	for path in dataPath:
		fp = os.path.join (path, filename)
		if os.path.isfile (fp): return fp
	if assertOnError: raise Error ('Data file "{}" not found in: {}'.format(filename, ', '.join(dataPath)))
	return None

# ------------------------------------------------------------------------------

def toolFilepath (filename, assertOnError=True):
	'''
	Returns the path to a filename in any tools directory in the directory tree.
	filename - name of the file
	assertOnError - True/False, throw an error when file not found
	Returns:
		1. full filepath of found file
		2. None if file not found and assertOnError is False
	'''
	global toolPath
	for path in toolPath:
		fp = os.path.join (path, filename)
		if os.path.isfile (fp): return fp
	if assertOnError: raise Error ('Tool file "{}" not found in: {}'.format(filename, ', '.join(toolPath)))
	return None
	
# ------------------------------------------------------------------------------

def _terminationHandler (signum, frame):
	'''Test timeout handler, force an error'''
	raise Error ("Termination timer expired")

# ------------------------------------------------------------------------------

def enhance_method (baseKlass, derivedKlass, method_name, replacement):
	'''replace a method with an enhanced version'''
	
	import new
	method = getattr (baseKlass, method_name)
	def enhanced (*args, **kwds):
		return replacement (method, *args, **kwds)
	setattr (derivedKlass, method_name, new.instancemethod (enhanced, None, baseKlass))

def method_wrapper (old_method, self, *args, **kwargs):
	'Call old method. Log PASS if old method does not cause exception'
	old_method (self, *args, **kwargs) # call original
	if 'msg' in kwargs.keys(): msg = kwargs['msg']
	else: msg = args[-1]
	log ('pass', msg) # display message parameter

# ------------------------------------------------------------------------------

class TestFrame (unittest.TestCase):
	'''Customization of standard unittest framework'''
	
	def __init__ (self, methodName='runTest'):
		'''Call parent class and enhance "assert" methods with test pass logging'''
		unittest.TestCase.__init__ (self, methodName)
		
		# -- For all the assert* methods, add a wrapper to log a PASS condition

		for rName in dir (unittest.TestCase):
			if rName.find ('assert') == 0 and rName.find ('assert_') != 0:
				z = getattr (unittest.TestCase,rName)
				enhance_method (unittest.TestCase, TestFrame, rName, method_wrapper)

# ------------------------------------------------------------------------------

class AutoTestResult (unittest.TestResult):
	"""Print formatted text results to the stream"""
	separator1 = '=' * 70
	separator2 = '-' * 70

	def __init__ (self, stream, descriptions):
		unittest.TestResult.__init__ (self)
		self.stream = stream
		self.descriptions = descriptions
		self.runTime = []
		self.startTime = None
		self.failfast = options.failfast

	def getDescription (self, test):
		if self.descriptions:
			return test.shortDescription() or str(test)
		else:
			return str (test)

	def startTest (self, test):
		# Setup termination timer (non windows)
#		if sys.platform not in ('win32','windows'):
#			killTimeout = 5
#			signal.signal (signal.SIGALRM, _terminationHandler)
#			signal.alarm (int(aspects.get('autotest.termination.seconds',0)))
		unittest.TestResult.startTest (self, test)
		log ('info', '==== ' + test._testMethodName + ' ====')
		self.startTime = time.time ()

	def stopTest (self, test):
		self.runTime.append ((test, time.time()-self.startTime))
		try:
			signal.alarm(0)          # Disable the alarm
		except:
			pass

	def addSuccess (self, test):
		unittest.TestResult.addSuccess (self, test)
		log ('pass', 'success')

	def addError (self, test, err):
		unittest.TestResult.addError (self, test, err)
		log ('error', err[1])

	def addFailure (self, test, err):
		unittest.TestResult.addFailure (self, test, err)
		log ('fail', err[1])
	
	def addSkip (self, test, reason):
		unittest.TestResult.addSkip (self, test, reason)
		log ('skip', reason)

	def printErrors (self):
		self.printErrorList ('FAIL', self.failures)
		self.printErrorList ('ERROR', self.errors)

	def printErrorList (self, flavour, errors):
		for test, err in errors:
			self.stream.writeln (self.separator1)
			self.stream.writeln ("{0} => test: {1}  desc: {2}".format (flavour, test._testMethodName, self.getDescription(test)))
			self.stream.writeln (self.separator2)
			self.stream.writeln ("{0}".format (err) )

# ------------------------------------------------------------------------------

class TestRunner:
	"""Run defined tests.

	It prints out the names of tests as they are run, errors as they
	occur, and a summary of the results at the end of the test run.
	"""
	def __init__ (self, stream=sys.stderr, descriptions=1):
		try:
			self.stream = unittest.runner._WritelnDecorator (stream) # for python 2.7
		except:
			self.stream = unittest._WritelnDecorator (stream)	# for python 2.6
		self.descriptions = descriptions

		varkeys = aspects.keys()
		if varkeys:
			biggest = max([len(x) for x in varkeys])
			log ('debug', 'Aspects:\n'+'\n'.join(['    {0}: {1}'.format (x.ljust(biggest), aspects[x]) for x in sorted(varkeys)]) )


	def run (self, theTestSuite):
		'''Run the given test case or test suite. Output problems, summary and XML for Jenkins'''
		global _gTestClassName
		
		result = AutoTestResult (self.stream, self.descriptions)
		startTime = time.time ()
		theTestSuite (result)				# Runs tests in suite
		totalTimeTaken = time.time () - startTime
		
		if len(result.failures) or len(result.errors):
			self.stream.writeln (result.separator2)
			self.stream.writeln ('==== Problems ====')
			result.printErrors ()
			self.stream.writeln (result.separator2)

		runCount   = result.testsRun
		failCount  = len (result.failures)
		errorCount = len (result.errors)
		skipCount  = len (result.skipped)

		# -- Start XML summary for Jenkins
		import xml.dom.minidom
		doc = xml.dom.minidom.Document()
		base = doc.createElement('testsuite')
		base.setAttribute('name', _gTestClassName)
		base.setAttribute('tests', str(runCount))
		base.setAttribute('failures', str(failCount))
		base.setAttribute('errors', str(errorCount))
		base.setAttribute('skipped', str(skipCount))
		doc.appendChild(base)

		if 'summary' in _gLogFilter:
			self.stream.writeln ('==== SUMMARY ====')

		# -- Get results for each test, put in return results and xml format
				
		allStatus = []
		for aTest in theTestSuite._tests:		
			status = 'pass'
			message = ''
			longMess = None
			for test,err in result.errors:
				if aTest == test:
					status = 'error'
					message = err.split('\n')[-2]
					longMess = err
					break
			for test,err in result.failures:
				if aTest == test:
					status = 'fail'
					message = err.split('\n')[-2]
					longMess = err
					break
			for test,msg in result.skipped:
				if aTest == test:
					status = 'skip'
					message = msg
					break
			for test,delta in result.runTime:
				if aTest == test:
					testTime = delta
					break
			allStatus.append  ({'name':aTest._testMethodName, 'status':status, 'time':testTime, 'msg':message})
			if 'summary' in _gLogFilter:
				self.stream.writeln ('== {} - {}, {:0f}, {}'.format (status, aTest._testMethodName, testTime, message))

			entry = doc.createElement('testcase')
			base.appendChild(entry)
			entry.setAttribute('classname', _gTestClassName)
			entry.setAttribute('name', aTest._testMethodName)
			entry.setAttribute('time','{:0f}'.format(testTime))
			r = status
			if r in ('fail','error','skip'):
				r == {'fail':'failure','skip':'skipped','error':'error'}[r]
				fentry = doc.createElement(r)
				fentry.setAttribute('type',message)
				if longMess: fentry.setAttribute('message',str(longMess))
				entry.appendChild(fentry)
		
		# -- Print summary of tests, output xml file
		
		if 'summary' in _gLogFilter:			
			self.stream.writeln ('==== TOTALS ====')
			self.stream.writeln ('count = {}'.format (runCount) )
			self.stream.writeln ('pass  = {}'.format (runCount - failCount - errorCount - skipCount))
			self.stream.writeln ("fail  = {}".format (failCount) )
			self.stream.writeln ("error = {}".format (errorCount) )
			self.stream.writeln ("skip  = {}".format (skipCount) )
			self.stream.writeln ('time  = {:0f}'.format (totalTimeTaken) )

		if 'xml' in _gLogFilter:
			open (_gTestClassName+'.xml','w').write(doc.toprettyxml(encoding='utf-8'))	# write this to file

		return allStatus

# ------------------------------------------------------------------------------

def parseConfigFile (filename):
	'''Parse and store a standard configuration file to the aspect globals'''
	
	global aspects, aspectsDefault
	
	if not os.path.isfile (filename):
		raise Error ('configuration file does not exist: {0}'.format(filename))

	# -- Set aspects to default value, merge with aspects from configuration file
	
	aspects = dotdictify (aspectsDefault)
	configConfig = ConfigParser.SafeConfigParser ()
	configConfig.readfp (open (filename))
	try:
		for k,v in configConfig.items ('aspects'):
			aspects[k] = v
	except:
		raise Error ('configuration file must contain "[aspects]" section tag')

# ------------------------------------------------------------------------------

def run (aModule):
	'''Local Standalone command and configuration parsing
	aModule - the test class derived from autotest class'''

	global _gTestClassName, options

	parser = optparse.OptionParser ('Usage: %prog [options] testname [, tn2 ...]')
	parser.add_option ('-c', '--config',  dest='config', default=None, help='Configuration file, Required')
	parser.add_option ('-l', '--log',   dest='log', default=None, help='logging filter in comma separated list w/o spaces')
  # added 13jul16 JDL
	parser.add_option ('-p', '--params',   dest='params', type=str, default=None, help='comma separated list w/o spaces of parameters to send to the test script')
	parser.add_option ('-f', '--failfast',   dest='failfast', action='store_true', default=False, help='Enable unittest\'s failfast option [True/False]')
	(options, args) = parser.parse_args()
	
	_gTestClassName = aModule.__name__
	fn = os.path.basename(sys.argv[0]).split('.')[0] 
	if fn != aModule.__name__:
		print '** WARNING: filename "{}" should match test class name: "{}"'.format(fn, aModule.__name__)

	if options.log: logFilter (options.log.split(','))
	if not options.config: raise Error ('configuration file required')
	parseConfigFile (options.config)

	if args:
		testNameList = args[0].split(',')
		suite = unittest.TestSuite (map(aModule,testNameList))
	else:
		suite = unittest.TestLoader ().loadTestsFromTestCase (aModule)

	TestRunner (stream=sys.stdout).run (suite)

# ------------------------------------------------------------------------------

def runAuto (aModule, testNameList, testAspects, logOptions):
	'''Remote test command and configuration parsing
	aModule      - the test class derived from autotest class
	testNameList - array of name of the test(s) to run
	testAspects  - dictionary of configuration variables for this test
	logOptions   - array of logging tags'''

	global _gLogFilter, aspects, aspectsDefault, _gTestClassName
	
	_gTestClassName = aModule.__name__
	_gLogFilter = logOptions
	aspects = dotdictify (aspectsDefault)
	for k,v in testAspects.items():
		aspects[k] = v
	suite = unittest.TestSuite (map(aModule,testNameList))
	return TestRunner (stream=sys.stdout).run (suite)
