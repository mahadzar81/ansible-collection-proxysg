from __future__ import absolute_import, division, print_function

__metaclass__ = type

__author__ = "Maza"
__version__ = "1.0"

import base64
import json
import os
import re
import sys
import time
import urllib2, urllib, cookielib
import HTMLParser
import autotest
import optparse
import telnetlib
import xml.dom.minidom
import paramiko
import types
import select
import ssl

from copy import deepcopy

from ansible.module_utils.urls import fetch_url
from ansible.module_utils._text import to_bytes, to_native
from ansible.module_utils.basic import env_fallback
from ansible.module_utils.connection import Connection, ConnectionError

# -- Regex prompt match objects

_reConfirm        = re.compile ( r"\(YES\): $", re.I )
_reConfirm2       = re.compile ( r"^[^\[]+(\[No\]|\[Yes\]|\[n\]):?$", re.I )
_reConfirmSSH     = re.compile ( r"\(yes\/no\)\? $", re.I )
_reRootPrompt     = re.compile ( r"^[^>\r\n$=<\"]{4,80}>$", re.I+re.M )
_reEnablePrompt   = re.compile ( r"^[^#\r\n$=<>\"]{4,80}#$", re.I+re.M )
_rePasswordEnable = re.compile ( r"^Enable Password:$", re.I+re.M )
_reTerminalPrompt = re.compile ( r"^Configuring.*\[terminal\]\?\s$", re.I+re.M )
_reConfigPrompt   = re.compile ( r"^[^#\r\n$=<>\"]{4,80}#\(config\)$", re.I+re.M )
_reConfigPrompt2  = re.compile ( r"^[^#\r\n$=<>\"]{4,80}#\(config[^\)]+\)$", re.I+re.M )
_reEnterOption    = re.compile ( r'Enter option: $', re.I )
_reMore           = re.compile ( r'\-\-More\-\-$', re.M )

# -- special mode for slow serial port processing

_reConfigPromptBit   = re.compile ( r"\(config\)$", re.I+re.M )
_reConfigPromptBit2  = re.compile ( r"\(config[^\)]+\)$", re.I+re.M )


# -- Context states for command line usage

CLI_ROOT        = 'CLI_ROOT'
CLI_ENABLE      = 'CLI_ENABLE'
CLI_CONFIG      = 'CLI_CONFIG'
CLI_CONFIG_TREE = 'CLI_CONFIG_TREE'
CLI_EXIT        = 'CLI_EXIT'


class Error (Exception): pass

class NotTextNodeError: pass


# ------------------------------------------------------------------------------

def getTextFromNode(node):
    """
    Scans through all children of the node and gathers the text. 
    If the node has non-text child-nodes, then NotTextNodeError is raised.
    
    Args:
        node: The DOM node to extract text from.
        
    Returns:
        str: The concatenated text from all child nodes.
        
    Raises:
        NotTextNodeError: If a non-text node is encountered.
    """
    if not node.hasChildNodes():
        return ""

    text_content = []
    for child in node.childNodes:
        if child.nodeType == child.TEXT_NODE:
            text_content.append(child.nodeValue)
        else:
            raise NotTextNodeError("Non-text node encountered: {}".format(child.nodeName))
    
    return ''.join(text_content).strip()

# ------------------------------------------------------------------------------

def nodeToDictionary (node):
	'''
    nodeToDic() scans through the children of node and makes a
    dictionary from the content.
    three cases are differentiated:
	- if the node contains no other nodes, it is a text-node
    and {nodeName:text} is merged into the dictionary.
	- if the node has the attribute "method" set to "true",
    then it's children will be appended to a list and this
    list is merged to the dictionary in the form: {nodeName:list}.
	- else, nodeToDic() will call itself recursively on
    the nodes children (merging {nodeName:nodeToDic()} to
    the dictionary).
    '''
	dic = {}
	ix = 1
	for n in node.childNodes:
		if n.nodeType != n.ELEMENT_NODE:
			continue
		if n.getAttribute("multiple") == "true":
			# node with multiple children:
			# put them in a list
			l = []
			for c in n.childNodes:
				if c.nodeType != n.ELEMENT_NODE:
					continue
				l.append(nodeToDictionary(c))
				dic.update({u.nodeName:l})
			continue
			
		try:
			text = getTextFromNode(n)
		except NotTextNodeError:
			# 'normal' node
			nm = n.nodeName
			if nm == 'cmd':
				nm = 'cmd{}'.format(ix)
				ix+= 1
			dic.update({nm:nodeToDictionary(n)})
			continue
	
		# text node
		dic.update({n.nodeName:text})
		continue
	return dic


# ------------------------------------------------------------------------------

class QDExpect:
	'''
	Duplicates the functions of telnetlib's expect function for the
	paramiko SSH connction libraries
	'''
		
	def __init__ (self, channel):
		'''channel - a paramiko channel object invoking shell'''
		
		self.buffer = ''
		self.channel = channel
		self.expect_write = self.channel.send
		self.expect_read = self.channel.recv
		self.fileno = self.channel.fileno

	def fill_buffer (self):
		self.buffer = self.buffer + self.expect_read2 (1024)

	def expect (self, reList, timeout=10):
		'''
		Read until one from a list of regular expresssions matches.
		reList - list of regular expressions, either compiled or strings, single or list
		timeout - optional timeout in seconds
		Returns: (match_list_index, match_object, text_before_match)
		'''

		timeout_time = time.time() + timeout

		if type(reList) == types.ListType or type(reList) == types.TupleType:
			reList = reList[:]
		else:
			reList = [reList]

		indices = range(len(reList))
		for i in indices:
			if not hasattr ( reList[i], "search" ):
				reList[i] = re.compile (reList[i])
		while 1:
			for i in indices:
				mo = reList[i].search (self.buffer)
				if mo:
					text_before = self.buffer[:mo.start()]
					self.buffer = self.buffer[mo.end():]
					return i, mo, text_before

			this_timeout = timeout_time - time.time ()
			if this_timeout <= 0: return -1, None, None
			selectin, selectout, selectexcept = select.select ([self], [], [], this_timeout)
			if len (selectin) == 0: return -1, None, None
			self.fill_buffer ()

	def expect_read2 (self, max_size=None):
		data = self.expect_read (max_size)
		return data

	def	read (self, max_size=None):
		if self.buffer:
			if max_size:
				size = min (max_size, len(self.buffer))
			else:
				size = len (self.buffer)
			data = self.buffer[:size]
			self.buffer = self.buffer[size:]
			return data
		else:
			return self.expect_read2 (max_size)

	def write (self, data):
		self.expect_write (data)

	def close (self):
		pass

# ------------------------------------------------------------------------------

class ProxyCommon:
	'''Common routines for ProxySGCLI and ProxySGHTTP'''

	# --------------------------------------------------------------------------

	def wait (self, initWait=15, pingWait=300, endWait=15):
		'''
		Wait for device to boot
		initWait - hold time before checking with ping
		pingWait - how long to wait for a ping
		endWait  - hold time before releasing back to main script
		return: (status, status_text)
		'''

		time.sleep (initWait)
		iTimer = 0
		if self.aspects.ipaddr == None: raise Error ('Need IP address to ping')
		while self._aPing (self.aspects.ipaddr) == False:
			iTimer += 2
			if (iTimer > pingWait):
				raise Error ('Wait for boot failed timeout')
		time.sleep (endWait)
		return True
	
	# --------------------------------------------------------------------------

	def _aPing (self, sIP):
		'''
		Ping (private routine) one... ping... only
		return: True - successful ping, False - no ping returned in 2 seconds
		'''
		
		pingCommands = { # ('ping command and grep', passing_return_value)
			"sunos":   ('ping {} 5 | grep alive >/dev/null', 0),
			"freebsd": ("ping -c 1 -t 2 {} | grep -E '1 packets received' >/dev/null", 0),
			"darwin":  ("ping -c 1 -t 2 {} | grep -E '1 packets received' >/dev/null", 0),
			"linux":   ("ping -c 1 -W 2 {} | grep -E '1 received' >/dev/null", 0),
			'linux2':  ("ping -c 1 -W 2 {} | grep -E '1 received' >/dev/null", 0),
			"windows": ("ping -n 1 -w 2000 {} | grep -E '1 (packets )?received' >/dev/null", 0),
			"win32":   ('ping -n 1 -w 2000 {} | find /I "Received = 1"', 0),
			}
		tos = sys.platform
		if tos not in pingCommands.keys(): raise Error ("Don't know how to PING on "+tos)
		command, value = pingCommands[tos]
		return os.system (command.format(sIP)) == value

	# --------------------------------------------------------------------------
	
	def checkForName (self, aspects, name):
		'''Check for existance of an aspect, return value'''
		value = aspects.get(name)
		if value == None:
			raise Error ('Need aspect: ' + name)
		return value


# ------------------------------------------------------------------------------

class ProxySGCLI (ProxyCommon):
	'''
	ProxySG connection object for command line access
	Usage:
	  p = proxysg.ProxySGCLI('proxysg_1')
	  or:
	  p = proxysg.ProxySGCLI (ipaddr='1.2.3.4', username='admin', password='admin')
	  
	  sgout = p.command ('show clock')
	  sgout = p.command ('show interface all', context=proxysg.CLI_ENABLE)
	  p.close ()
	'''

	# --------------------------------------------------------------------------

	def __init__ (self, device=None, ipaddr=None, username=None, password=None, cliaccess=None, enablePassword=None, serial=None, loginTimeout=10, promptTimeout=10, commandTimeout=120):
		'''
		Initialize proxy connection object for command line access
		
		device - autotest device name, ie "proxysg_1"
		ipaddr - IP address of device
		username - user name
		password - pass word
		enablePassword - password used for enable command
		cliaccess - use serial or ssh 		
		serial - string containing address and port to serial server, format:  <ipaddr>:<port>
		
		When the device paramter is utilized then address/username/password parameters are taken from
		the aspects dictionary. The global autotest aspects dictonary is used.

		Hierarchy: parameters > global aspects > defaults
		
		Username, password and enablePassword are required (either passed in or from aspects)
		ipaddr or proxysg_1.ipaddr is required when cliaccess == ssh
		serial or proxysg_1.serial is required when cliaccess == serial
		'''
		
		self.device         = device
		self.connector      = None
		self.loginTimeout   = loginTimeout
		self.promptTimeout  = promptTimeout
		self.commandTimeout = commandTimeout
		self.xmlData        = {}
		self.context        = None
		self.debugLevel     = 0
		self.info           = {}
		self.aspects        = autotest.dotdictify()
		self.sgcli          = self

		# -- First, parameters
		asp = self.aspects
		asp.device    = device
		asp.ipaddr    = ipaddr
		asp.username  = username
		asp.password  = password
		asp.cliaccess = cliaccess
		asp.serial    = serial
		asp.password_enable = enablePassword

		# -- Second, configuration properties for device, if properties are empty
		if device and autotest.aspects.get(device):
			for k,v in autotest.aspects[device].items():
				if asp.get(k) == None:
					asp[k]=v

		# -- And lastly, defaults, if empty (do not set defaults in __init__ definition)
		if asp.username == None: asp.username = 'admin'
		if asp.password == None: asp.password = 'admin'
		if asp.password_enable == None: asp.password_enable = 'admin'
		if asp.cliaccess == None: asp.cliaccess = 'ssh'

		# -- Validations
		if asp.cliaccess == 'ssh':
			if asp.ipaddr == None: raise Error ('require "ipaddr" IP address')

		elif asp.cliaccess == 'serial':
			if asp.serial == None: raise Error ('require "serial" <ipaddr>:<port>')
			if not re.search ('^[\d\.]+\:\d+$', asp.serial):		# not ready for IPv6
				raise Error ('bad format of "serial" <ipaddr>:<port>: {}'.format(asp.serial))
		else:
			raise Error ('unknown "cliaccess": {}'.format(asp.cliaccess) )
		
		if self.device: self.index = self.device[-1]
		else: self.index = ''

	# --------------------------------------------------------------------------------

	def _goThroughLogin ( self ):
		'''Private routine to parse though all login username/password prompts to command prompt'''		
		
		# -- Login with SSH paramiko libraries, works with UNIX and Windows systems.
		# -- Create a shell style connector and our own expect parser.
		
		if self.aspects.cliaccess == 'ssh':
			self.client = paramiko.SSHClient ()
			self.client.set_missing_host_key_policy (paramiko.AutoAddPolicy())
			self.client.connect (self.aspects.ipaddr, username=self.aspects.username, password=self.aspects.password)
			channel = self.client.invoke_shell(width=1000, height=1000)			# returns channel object
			self.connector = QDExpect (channel)		# make Quick and Dirty expect wrapper
			while 1:
				reIndex, matchObj, text = self.connector.expect([_reRootPrompt], timeout=self.loginTimeout )
				if reIndex == 0:													# caught command prompt
					self.context = CLI_ROOT
					break
				else:
					raise Error ('SSH timeout on log-in')
		
		# -- Login through serial port. Tricky because:
		# -- 1. State is unknown, send a return, determine state by returned prompt.
		# -- 2. Initial connect requires three returns for a login menu, this can be a problem.
		# -- 3. The enable and configure prompts are non-unique, a configure prompt can trigger
		# --    a enable prompt on the slow connection, thus wait for additional characters.
		
		elif self.aspects.cliaccess == 'serial':
			saddr, sport = self.aspects.serial.split(':')
			self.connector = telnetlib.Telnet (saddr, int(sport), timeout=self.loginTimeout)
			self.connector.set_debuglevel (self.debugLevel)
			self.connector.write ('\r')
			count = 0
			while 1:
				reIndex, matchObj, text = self.connector.expect ([
						_reEnterOption,
						_reConfigPrompt,
						_reConfigPrompt2,
						_reRootPrompt,
						_reEnablePrompt,
						_rePasswordEnable,
						_reMore], 3)
				if   reIndex == -1: self.connector.write ('\r')
				elif reIndex == 0: self.connector.write ('1')
				elif reIndex == 1:		# Configuration prompt (top level)
					self.context = CLI_CONFIG
					break
				elif reIndex == 2:		# Configuration prompt (deeper)
					self.context = CLI_CONFIG_TREE
					break
				elif reIndex == 3:		# Root prompt
					self.context = CLI_ROOT
					break
				elif reIndex == 4:		# Enable prompt, special case
					# -- Wait 1/4 second for additional characters of config prompts
					self.context = CLI_ENABLE
					reIndex, matchObj, text = self.connector.expect ([
						_reConfigPromptBit, 
						_reConfigPromptBit2], 0.25)
					if reIndex == 0:
						self.context = CLI_CONFIG
					elif reIndex == 1:
						self.context = CLI_CONFIG_TREE
					break
				elif reIndex == 5:		# Enable password prompt, acknowledge
					self.connector.send (self.aspects.password_enable+'\r')
					break
				elif reIndex == 6:		# --More-- prompt, quit from here
					self.connector.write ('q')
				else: raise Error ('Serial login timeout')
				
				count += 1
				if count > 5: raise Error ('Serial login problem')

	# --------------------------------------------------------------------------------
	
	def command (self, cmdLine, context=None, timeout=None, confirmation=1):
		'''
		Send a CLI command to the proxy. Match requested context to current self.context
		by sending appropriate commands.
		
		cmdLine - CLI command, a return is always added
		context - modal context state to use. None=Use current context
		timeout - how long before forcing a timeout error (default use initial values)
		confirmation - what to say on confirmation challenge, 0=no, 1=yes
		Returns: output of command, without command echo or prompt
		
		Five context states:
			CLI_ROOT   - post log in mininum configuration and view
			CLI_ENABLE - base configuration and view
			CLI_CONFIG - system configuration context
			CLI_CONFIG_TREE - deeper levels of configuration
			CLI_EXIT   - exit command line and drop connection
		'''

		if timeout == None: timeout = self.commandTimeout
		if self.connector == None: self._goThroughLogin ()

		if context not in (None, CLI_ROOT, CLI_ENABLE, CLI_CONFIG, CLI_CONFIG_TREE, CLI_EXIT):
			raise Error ('Bad command Context')
			
		# -- Match requested context to current system context level

#		autotest.log ('debug', 'CONTEXT, cur: {}, cmd: {}'.format(self.context,context) )
		while context and context != self.context:
		
			if self.context == CLI_ROOT and context in (CLI_ENABLE, CLI_CONFIG):
				self._cmd ('enable', context=context, timeout=timeout, confirmation=confirmation)
				
			elif self.context == CLI_ENABLE and context in (CLI_CONFIG,):
				self._cmd ('configure terminal', context=context, timeout=timeout, confirmation=confirmation)
			
			elif self.context in (CLI_ROOT, CLI_ENABLE) and context in (CLI_EXIT):
				break
				
			else:
				self._cmd ('exit', context=context, timeout=timeout, confirmation=confirmation)
		
		# -- Now do the command
		return self._cmd (cmdLine, context=context, timeout=timeout, confirmation=confirmation)


	# --------------------------------------------------------------------------------

	def _cmd (self, cmdLine, context=None, timeout=None, confirmation=1):
		'''Internal routine. Send command to ProxySG and wait for prompt. Set context
		based on prompt.'''
		
		autotest.log ('sgcmd', cmdLine, self.index) # + '    CONTEXT:{}'.format(context))
		
		if self.aspects.cliaccess == 'ssh':
		
			# -- Send command and wait for command or confirm prompt
			self.connector.write (cmdLine+'\r')
	
			# -- Exit context, no return prompt, drop connection
			if context == CLI_EXIT:
				time.sleep(2)
				self.close()
				return ''
			
			store = ""
			while 1:
				reIndex, reMatchObj, reText = self.connector.expect ( [
							_reConfirm2,
							_reRootPrompt,
							_reEnablePrompt,
							_rePasswordEnable,
							_reConfigPrompt,
							_reConfigPrompt2,
							_reMore,
							], timeout=timeout )

				if reText: store += reText
				
				if reIndex==0:					# Confirmation prompt
					if confirmation == 0:
						self.connector.write ('no\r')
					else:
						self.connector.write ('yes\r')
						
				elif reIndex == 1:				# Root level prompt
					self.context = CLI_ROOT
					break
					
				elif reIndex == 2:				# Enable level prompt
					self.context = CLI_ENABLE
					break
					
				elif reIndex == 3:				# Enable password prmpt
					self.connector.write (self.aspects.password_enable+'\r')
					
				elif reIndex == 4:				# Configuration prompt (top level)
					self.context = CLI_CONFIG
					break
					
				elif reIndex == 5:				# Configuration prompt (lower)
					self.context = CLI_CONFIG_TREE
					break
					
				elif reIndex == 6:				# --More-- prompt
					self.connector.write (' ')
					
				elif reIndex == 7: 				# EOF
					self.context = CLI_ROOT
					self.close ()
					break
				
				elif reIndex == -1: # timeout
					raise Error ('ProxySG SSH connection timed out: {} {}'.format(self.aspects.device, self.aspects.ipaddr))
			
			# -- Remove echoed command line and extra blank lines
			store = store[store.find('\n'):].strip()
			autotest.log ('sgout', store, self.index)
			return store
			
		elif self.aspects.cliaccess == 'serial':
		
			# -- Send command and wait for command or confirm prompt
			self.connector.write (cmdLine+'\r')

			# -- Exit context, no prompt, drop connection
			if context == CLI_EXIT:
				time.sleep(2)
				self.close()
				return ''
			
			store = ''
			while 1:
				reIndex, reMatchObj, reText = self.connector.expect ([
							_reConfirm2,
							_reConfigPrompt,
							_reConfigPrompt2,
							_reRootPrompt, 
							_reEnablePrompt,
							_rePasswordEnable,
							_reMore,
							], timeout=timeout)

				if reText: store += reText
					
				if reIndex == 0:				# Confirmation prompt
					if confirmation == 0:
						self.connector.write ('no\r')
					else:
						self.connector.write ('yes\r')
						
				elif reIndex == 1:				# Configuration prompt (top level)
					self.context = CLI_CONFIG
					break
					
				elif reIndex == 2:				# Configuration prompt (lower)
					self.context = CLI_CONFIG_TREE
					break
					
				elif reIndex == 3:				# Root level prompt
					self.context = CLI_ROOT
					break
					
				elif reIndex == 4:				# Enable level prompt
					# -- Possible false match, Look for the additional characters of config prompts
					self.context = CLI_ENABLE
					reIndex, reMatchObj, reText = self.connector.expect ([
						_reConfigPromptBit, 
						_reConfigPromptBit2], 1)
					if reText: store += reText[reText.find('\n'):].strip()
					if reIndex == 0:   self.context = CLI_CONFIG
					elif reIndex == 1: self.context = CLI_CONFIG_TREE
					break
					
				elif reIndex == 5:				# Enable password prompt
					self.connector.write (self.aspects.password_enable+'\r')
					
				elif reIndex == 6:				# --More-- prompt
					self.connector.write (' ')
					
				elif reIndex == 7: #EOF
					self.context = CLI_ROOT
					self.close ()
					break
				
				elif reIndex == -1: # timeout
					raise Error ('ProxySG serial connection timed out')

			# -- Remove "--More--" junk, echoed command line, prompt and extra blank lines
			store = re.sub('--More--\x08{8}\x20{8}\x08{8}', '', store)
			store = store[store.find('\n'):store.rfind('\n')].strip()
			autotest.log ('sgout', store, self.index)
			return store

	# --------------------------------------------------------------------------

	def close (self):
		'''Close the ProxySG connector'''
		
		if self.connector:
			self.connector.close ()
		self.connector = None
	

	# --------------------------------------------------------------------------
	def commandBatch (self, context, batch, check=None):
		'''
		Send a batch of commands and optionally check the output of the last one
		context - the batch first command's context
		
		batch - list of commands to execute
		check - string or list, when present, one in list be in the output of the last command
				otherwise an error will be raised
		Returns: output of last command
		'''
	
		for cmd in batch:
			output = self.command (cmd, context)
			context = None
			
		if check:
			if type(check) == str: check = (check,)
			match = False
			for item in check:
				match |= (re.search (item,output,re.I) != None)
			if not match:
				raise Error ('command: {} did not return: {} in output'.format (cmd, check))
		return output
		
	# --------------------------------------------------------------------------

	def _setupXMLdata (self, file):
		'''Read xml data file for shortcuts and stuff'''
		
		dom = xml.dom.minidom.parse (file)
		self.xmlData = nodeToDictionary (dom)

		self.xmlvars = self.xmlData['CLI']['vars']
		self.contexts = self.xmlData['CLI']['Contexts']
		self.shortcuts = self.xmlData['CLI']['Shortcuts']
		# -- xml to dictionary
	
	# --------------------------------------------------------------------------

	def shortcut (self,sk):
		'''Lookup shortcut from XML file and send commands (not used)'''
		
		replaceList = {
			'CR':'', 
			'HTTPCONSOLEPORT': self.aspects.get ('httpconsoleport',''),
			'IMAGEURL': self.aspects.get ('imageurl',''),
			'PROXYIP' : self.aspects.get ('ipaddr',''),
			}
		print self.aspects
		
		print 'shortcut:',sk
		c = self.shortcuts.get(sk)
		print ' using context:', c['context']
		context = c['context']
		ix = 1
		while 1:
			cmdSet = c.get('cmd{}'.format(ix))
			ix += 1
			if not cmdSet: break
			cmd = cmdSet['output']
			# -- confirugation substitutions
			while 1:
				m = re.search ('{([^}]+)}', cmd)
				if not m: break
				cmd = cmd.replace ('{'+m.group(1)+'}', replaceList[m.group(1)])
			self.command (cmd, c=context)
			context = None		# context only for the first command
		
	# --------------------------------------------------------------------------

	def loadBuild (self, build, type='system.bcsi'):
		'''
		Load software from the build server.
		
		build - number corresponding to software on build server, format "#####", or
		        a URL to a build (not implemented)
		type - Type of build 64bit, 32bit, debug, signed, etc. (not implemented)
		return: (status, statusText, loadedVersion, loadedBuild)

		This is a smart loader:
		1. image file is from the build server VIA http
		   or from a URL specified in the build field
		2. It will not reload the same build
		3. It will switch to a build already loaded on the system
		4. The new image version is verified against the intended version
		'''
		
		# -- DNS server for system updates (used in build loading)
		# -- URL to build server pages (used in build loading)
		kDnsServer = '10.2.2.10'
		kBuildArchiveURL = 'http://buildarchive.bluecoat.com/'
		kBuildInfoURL = '''http://cachezilla.bluecoat.com/XMLInterface.cgi?build_id={}&json=1'''

		m = re.search (r'^\d+$', build)
		if not m: raise Error ('build number format: {}'.format(build))
		
		# -- Return if installed build and expectd build match

		currentVersion, currentBuild = self.getVersionBuild ()
		if str(build) == str(currentBuild): return currentVersion, currentBuild

		readyToRestart = False
		# -- put code to load a build from a URL here

		# -- Look at currently installed systems, use if match
		if not readyToRestart:		
			self.command ('installed-systems', context='CLI_CONFIG')
			sgout = self.command ('view')
			for n in range (1,6):
				m = re.search ('{}\. Version: SGOS [\d\.]+, Release ID: {}'.format(n, build), sgout, re.M)
				if m:
					self.command ('default {}'.format(n))
					self.command ('exit')
					readyToRestart = True
					break

		# -- Look for builds on build server

		if not readyToRestart:
			buildLinks = {}
			# -- Get build information from cachezilla, to contruct a build server link
			import json
			data = urllib2.urlopen(kBuildInfoURL.format(build)).read()
			x = json.loads (data) ['Build']
			if 'branch' not in x: raise Error ('build XML data: ' + x['msg'])
			branch = x['branch']
#			build = x['build_id']
			# -- Look for 64bit and 32bit image directories, put those in link dictionary
			
			baseLink = kBuildArchiveURL + branch + '.' + build + '/wdir/images/bin/'
			try:
				p = urllib2.urlopen (baseLink)
				exists = True
			except urllib2.HTTPError:
				exists = False
			if exists and p.code == 200:
				cpudata = p.read()
				cpu = ('x86_64','x86')
				for acpu in cpu:
					if re.search('{}/'.format(acpu), cpudata):
						nl = baseLink+'{}/sgos_native/release/'.format(acpu)
						data = urllib2.urlopen(nl).read()				
						m = re.search('(gcc_v\d+\.\d+\.\d+/)',data)
						if m: buildLinks[acpu] =  nl + m.group(1) + 'sysimg/' + type
						else: raise Error ('could not parse build link')	
			else:
				# -- Look for older build versions, get model number to select proper image directories
				# -- (not tested)
				out = self.command ('show advanced-url /Diagnostics/Hardware/Info')
				m = re.search (r'Model:\s+([0-9]+)', out)
				if not m: raise Error ('could not find model number')
				buildFile = m.group(1) + '.chk'
				oldLink = kBuildArchiveURL + branch + '.' + build + '/wdir/' + buildFile
				try:
					p = urllib2.urlopen (oldLink)
					exists = True
				except urllib2.HTTPError:
					exists = False
				if exists and p.code == 200:
					buildLinks['old'] = oldLink
				else:
					raise Error ('could not find proxysg build on build server for branch: {}, build: {}'.format(branch, build))

			# -- Make sure that DNS server is configured so as to find build server

			self.command ('dns-forwarding', context='CLI_CONFIG')
			self.command ('edit primary')
			sgout = self.command ('view')
			if sgout.find(kDnsServer) == -1:
				self.command ('add server '+kDnsServer)
			self.command ('exit')
			self.command ('exit')

			# -- Walk through build link dictionary by perfered type
			# -- % incompatible? check next build, other errors cause fault

			for bt in ('x86_64','x86','old'):
				if bt in buildLinks:
					self.command ('upgrade-path {}'.format(buildLinks[bt]), context='CLI_CONFIG')
					sgout = self.command ('load upgrade ignore-warnings')
					if re.search ('incompatible', sgout, re.I): continue
					if re.search ('% ', sgout, re.M): raise Error (sgout)
					if re.search ('Failed', sgout, re.I): raise Error (sgout)
					readyToRestart = True
					break

		# -- Bad news
		if not readyToRestart: raise Error ('could not find build to load')
		
		# -- Restart the box, check new build to confirm load
		self.command ('',context='CLI_ENABLE')
		self.command ('restart upgrade', context='CLI_EXIT')	# special context as no return prompt	
		self.wait (endWait=300)
		self._goThroughLogin () #JDL- connector is likely to be invalid after restart, so go back through login
		cVersion,cBuild = self.getVersionBuild ()
		if str(cBuild) != str(build):
			raise Error ('load build did not match, expected: {}, have: {}'.format(build, cBuild))
		return cVersion, cBuild

	# --------------------------------------------------------------------------

	def getVersionBuild (self):
		'''Returns: (version, build)'''

		sgout = self.command ('show version', context='CLI_ENABLE')
		m = re.search ('Version: SGOS (?P<version>[\d\.]+)[\s\S]+Release id:\s+(?P<build>[\d]+)', sgout)
		if not m: raise Error ('could not get version str')
		return m.group('version'), m.group('build')

	# --------------------------------------------------------------------------

	def restartSG (self, command = "restart regular"):
		''' Restart proxySG
		command - 'restart regular'(default) or 'restart upgrade'
		'''
		 
		self.command ('', context='CLI_ENABLE')
		self.command (command, context='CLI_EXIT')				
		autotest.log ('debug', "Wait for SG to come up after the restart...")
		self.wait()
	
	# --------------------------------------------------------------------------

	def getSgVer (self):
		'''Get ProxySG's version number
		Returns: SGOS ver in x.x.x.x format
		'''
		
		retVal = self.command ('show version', context='CLI_ENABLE')
		match = re.search ("(?mi)^Version:\s+SGOS\s+([\d\.]+)\s", retVal)
		if match: return match.group(1)
		raise Error ('could not find SGOS version in: ' + retVal)

	# --------------------------------------------------------------------------

	def getInfo (self):
		'''
		Walk through a set of regex patterns to match system information from device.
		Store data into self.info
		Note: initial version
		'''

		patterns = (
			('version',      r'''^Version:\s+SGOS\s+([0-9\.]+)'''),		# 6.2.15.2 or 99.99.99.99
			('build',        r'''^Release id:\s+([0-9]+)'''),	# 123456
			('serialnumber', r'''^Serial\snumber:\s+([0-9\-]+)'''),		# 1234567890
			)

		sgout = self.command ('show version', 'CLI_ENABLE')
		self.info = autotest.dotdictify({})
		for name,repat in patterns:
			match = re.search (repat, sgout, re.I+re.M)
			if match: self.info[name] = match.group(1)
			else: raise Error ('getInfo, could not match: '+name)

# ------------------------------------------------------------------------------

class ProxySGHTTP (ProxyCommon):
	'''
	ProxySG connection object for web access
	Usage:
	  p = proxysg.ProxySGHTTP ('proxysg_1')
	  or:
	  p = proxysg.ProxySGHTTP (ipaddr='1.2.3.4', username='admin', password='admin')
	  
	  pagedata = p.getPage ('/Diagnostics/Hardware/Info')
	  p.close ()  
	'''

	# --------------------------------------------------------------------------

	def __init__ (self, device='', protocol=None, ipaddr=None, port=None, username=None, password=None):
		'''
		Initialize proxy connection object for HTTP access.
		
		device - autotest device name, ie "proxysg_1"
		protocol - http, https (default=https)
		ipaddr - IP address of device
		port - IP port of device (defaults to 8082)
		username - user name
		password - pass word
		aspects - dictionary of configuration variables (defaults to autotest.aspects)
		
		When the device paramter utilized then the ipaddr/username/password parameters are taken from
		the aspects dictionary. aspects can be passed in or the glocal autotest aspects dictonary is used.

		Hierarchy: parameters > autotest aspects > defaults
		'''
		
				
		self.aspects = autotest.dotdictify()
		asp = self.aspects
		asp.username = username
		asp.password = password
		asp.protocol = protocol
		asp.ipaddr   = ipaddr
		asp.port     = port
		
		if device:
			for k,v in autotest.aspects[device].items():
				if asp.get(k) == None: asp[k]=v
				
		if asp.username == None: asp.username = 'admin'
		if asp.password == None: asp.password = 'admin'
		if asp.protocol == None: asp.protocol = 'https'
		if asp.port     == None: asp.port     = 8082

		# -- Install managers for password authentication and cookie management
		# -- These will automatically keep session for subsequent web access

		self.passMan = urllib2.HTTPPasswordMgrWithDefaultRealm ()
		self.passMan.add_password (None,
			'https://{}:{}'.format (self.aspects.ipaddr,self.aspects.port),
			self.aspects.username, self.aspects.password)
		self.authHandler = urllib2.HTTPBasicAuthHandler (self.passMan)
	
		self.cookieJar = cookielib.CookieJar ()
		self.cookieMan = urllib2.HTTPCookieProcessor (self.cookieJar)

                # Build the SSL context to disable certificate verification
                # This is a little bit of a security hole ... Need to have a better means of addressing this!
                self.ctx = ssl.create_default_context()
		self.ctx.check_hostname = False
		self.ctx.verify_mode = ssl.CERT_NONE

		self.opener = urllib2.build_opener (self.authHandler, self.cookieMan, urllib2.HTTPSHandler(context=self.ctx))


	# --------------------------------------------------------------------------

	def getPage (self, url):
		'''
		Retrieve page data from a url
		url - /direcotry/file portion of url, must start with slash
		'''

		fullUrl = '{}://{}:{}{}'.format (self.aspects.protocol, self.aspects.ipaddr, self.aspects.port, url)
		request = urllib2.Request (url=fullUrl)
		request.add_header('User-agent', 'Mozilla/4.0 (compatible; MSIE 5.5; Windows NT)')
		return self.opener.open (request).read ()

		# -- need to figure out get vs post, currently defaulting to GET

# 		if method == 'GET':
# 			f = urllib2.urlopen (request)
# 
# 		elif method == 'POST':
# 			opener.addheaders = [('Content-type', 'form-data')]
# 			params = urllib.urlencode({'LOGIN.USERNAME':user, 'LOGIN.PASSWORD':password})
# 			f = urllib2.urlopen (fullUrl, params)		
# 		data = f.read ()
# 		f.close ()
# 
# 		return data

	def getConnectionPool(self, pool):
		'''Get ADN connection pools from adn/show/tunnel/cpm'''
		
		conList = []
		tunnelPool = self.getPage('/adn/show/tunnel/cpm?stats_mode=5').splitlines ()
		for i in range(len(tunnelPool)):
			if tunnelPool[i].find (pool) >= 0:
				autotest.log('debug', "Requested {} found".format(pool))
				if tunnelPool[i+1].find ("Total:") >= 0:
					autotest.log('debug', "No {} found".format(pool) )
					conList.append (tunnelPool[i+1])
				else:
					i = i + 2
					while tunnelPool[i].find ("CPMR<") >= 0:
						conList.append (tunnelPool[i])
						i = i+1
					if tunnelPool[i].find ("Total") >= 0:
						conList.append (tunnelPool[i]) # appends the total # of connections
				break
		return conList
	
	# --------------------------------------------------------------------------

	def getTcpConnections(self):
		'''
		Find and get all tcp connections from url /tcp/connection
		'''
		conRe = re.compile (r"(?im)(\w+)\s+(\d+)\s+(\d+)\s+((?:\d+\.\d+\.\d+\.\d+\.\d+)|(?:\*\.\d+))\s+((?:\d+\.\d+\.\d+\.\d+.\d+)|(?:\*\.\*))\s+(\w+)\s+(\w.*$)")
		sCon = self.getPage('/tcp/connections')
		retVal = []
		if sCon != None:
			retVal = conRe.findall(sCon)
		return retVal
	
	# --------------------------------------------------------------------------

	def getMacAddress(self, interfaceId):
		'''
		Get MAC address of given interface from url /Diagnostics/Hardware/Info
		'''
		#macRe = re.compile (r"(?im)(\w+)\s+(\d+)\s+(\d+)\s+((?:\d+\.\d+\.\d+\.\d+\.\d+)|(?:\*\.\d+))\s+((?:\d+\.\d+\.\d+\.\d+.\d+)|(?:\*\.\*))\s+(\w+)\s+(\w.*$)")
		sHwInfo = self.getPage('/Diagnostics/Hardware/Info?stats_mode=5')
		if sHwInfo != None:
			macObj = re.search('Interface\s+{}:.+MAC\s+(([a-fA-F0-9]{2}[:|\-]?){6})'.format(interfaceId), sHwInfo)
			if macObj:
				return macObj.group(1)
			else:
				raise Error ('could not get MAC address for interface {}'.format(interfaceId))
		else:
			raise Error ('nothing returned from /Diagnostics/Hardware/Info')
	
	# --------------------------------------------------------------------------

	def close (self):
		'''Close the ProxySG connector'''
		pass


# ------------------------------------------------------------------------------

class SGHTMLParser (HTMLParser.HTMLParser):
	'''
	Parse simple html table structure to nested array
	usage:
		p = SGHTMLParser ()
		nestedArray = p.parse ( outputFromAPage )		
	'''
	
	_result = None
	_stack = None
	_curtag = None

	def parse (self, data):
		'''Clear old results, process HTML data, return data as nested array
		data - HTML page with nested tables'''
		self._result = None
		HTMLParser.HTMLParser.feed (self, data)
		return self._result

	def handle_starttag (self,tag, attr):
#		print '.. start', tag, self.result
		if tag in ('table','tr'):
			if self._result == None:
				self._result = []
				self._stack = [self._result]
			else:
				t = []
				self._stack[-1].append(t)
				self._stack.append(t)
		self._curtag = tag
		
	def handle_endtag (self, tag):
#		print '.. end:', tag, self.result
		if tag in ('table','tr'):
			self._stack.pop()
		self._curtag = None
		
	def handle_data (self, data):
#		print '.. data:', data, self.result, self.curtag
		if self._curtag == 'td':
			self._stack[-1].append(data.strip())


# ==============================================================================

if __name__ == '__main__':

	parser = optparse.OptionParser (usage='%prog [options] <proxy_ipaddr> [<command>]\n{}'.format(__doc__))
	parser.add_option ('-l', '--log',      dest='log', default='', help='logging')
	parser.add_option ('-b', '--build',    dest='build', help='build number to load')
	parser.add_option ('-a', '--access',   dest='access', default=None, choices=('ssh', 'serial'), help='communication access method, choice: serial or ssh, default: ssh')
	parser.add_option ('-u', '--username', dest='username', default=None, help='login username, default: admin')
	parser.add_option ('-p', '--password', dest='password', default=None, help='login password, default: admin')
	parser.add_option ('-e', '--enable',   dest='enable', default=None, help='enable password, default: admin')
	parser.add_option ('-s', '--serial',   dest='serial', default=None, help='serial access address & port, format: <ipaddr>:<port>')
	parser.add_option ('-x', '--x',        dest='x', type='int', default=None, help='run an internal test')
	parser.add_option ('-c', '--config',   dest='config', default=None, help='configuration file')
	parser.add_option ('-d', '--device',   dest='device', default='proxysg_1', help='configuration file device name, default: proxysg_1')
	parser.add_option ('-i', '--info',     dest='info',     default=False, action='store_true', help='print system information')
	(options, args) = parser.parse_args ()

	if options.config:
		autotest.parseConfigFile(options.config)
		ipaddr = None
	else:
		if len(args) == 0:
			parser.print_help()
			sys.exit(1)
		ipaddr = args[0]
	
	sg = ProxySGCLI (
		device    = options.device,
		ipaddr    = ipaddr,
		cliaccess = options.access,
		username  = options.username,
		password  = options.password,
		serial    = options.serial,
		enablePassword = options.enable,
		)

	if options.log:
		autotest._gLogFilter = options.log.split(',')
	
	if options.build:
		sg.loadBuild (options.build)
	
	# -- Web page fetch test
	
	if options.x == 1:
		sg1 = ProxySGHTTP (ipadddr=args[0], port=8081, username=options.username, password=options.password)
		sg1.getPage ('/')
		sg1.getPage ('/Accesslog/tail/main')
		sg1.getPage ('/FTP/Info')
	#	print sg1.getPage ('/Sysinfo')
	
		print sg1.getPage ('/SYSINFO/Version')
		print sg1.getPage ('/Diagnostics/CPU_Monitor/Statistics')

		p = SGHTMLParser ()
		print p.parse ( sg1.getPage ('/Diagnostics/CPU/Statistics') )		
		print p.parse ( sg1.getPage ('/Diagnostics/Hardware/Info') )
		
	# -- CLI command execution test
	
	if options.x == 2:
		print sg.command ('show clock')
		print sg.command ('show cpu')
		print sg.command ('show sessions', context=CLI_ENABLE)
		print sg.command ('test http get http://' + gBuildArchiveURL + '/')
		print sg.context
		
	if options.x == None and len(args) > 0:
		start = 1
		if options.config: start = 0
		for cmd in args[start:]:
			print sg.command (cmd)

	if options.info:
		sg.getInfo()
		for k,v in sg.info.items():
			print '{}: {}'.format(k,v)

    