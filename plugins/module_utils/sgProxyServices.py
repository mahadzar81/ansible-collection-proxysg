'''
ProxySg proxy services library
'''

__author__ = 'Maza'
__version__ = '1.0.1'

import re
import os, sys
import autotest

class Error (Exception): pass

class SgProxyServices:
	'''
	Example (stand alone):

	sg1 = proxysg.ProxySGCLI('proxysg_1')
	services1 = sgProxyServices.SgProxyServices(sg1)
	print services1.viewProxyServices ('cifs')
	
	Example (aggigrate with proxysg)
	
	class TestCLI (proxysg.ProxySGCLI, sgProxyServices.SGProxyServices): pass
	sg1 = TestCLI ('proxysg_1')
	print sg1.viewProxyServies ('cifs')
	
	'''

	serviceConfigRE = re.compile (r"(?im)(\w.+\:)\s+(.+)\s*$") 
	serviceActionRE3 = re.compile (r"(?im)((?:\<\w+\>)|(?:\d+\.\d+\.\d+\.\d+)|(?:\d+\.\d+\.\d+\.\d+\/\d+))\s+(\d+)\s+(\w+)\s*$")    


	# --------------------------------------------------------------------------

	def __init__ (self, sgcli):
		'''link SG command routine'''
		
		self.sgcli = sgcli
		self.command = sgcli.command
		
	# --------------------------------------------------------------------------

	def editProxyServices (self, service):
		'''Enter Edit mode of a given service
		service - a proxy service name 
		Returns: true or raise error
		'''
			
		self.command("proxy-services", context='CLI_CONFIG')
		retVal = self.command('edit "{}"'.format(service))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		return True

	# --------------------------------------------------------------------------

	def viewProxyServices (self, service):
		'''View service settings
		service - a proxy service name e.g http, ftp
		Returns:  List of tuple - [('Service Name:','value'),('Service Group:','value'),
									('Proxy:','value'),('Attributes:','value')]
		'''
		
		serviceList = []
		self.command("proxy-services", context='CLI_CONFIG')
		retVal = self.command("edit " + service)
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		retVal = self.command ("view")
		serviceList = self.serviceConfigRE.findall (retVal)
		return serviceList

	# --------------------------------------------------------------------------

	def viewProxyServiceAction (self, service):
		'''View service action settings
		service - service to view
		Returns: List of tuple - e.g. [('<All>', '80', 'Bypass'), ('<Explicit>', '8080', 'Bypass')]
		'''
		
		actionList = []
		self.command("proxy-services", context='CLI_CONFIG')
		retVal = self.command('edit "{}"'.format (service))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		retVal = self.command ("view")
		actionList = self.serviceActionRE3.findall (retVal)
		
		return actionList

	# --------------------------------------------------------------------------

	def editServiceAction (self, service, destinationIP, portRange, action):
		'''Edit a service's action, destinationIP and portRange
		service: http, ftp, etc
		destinationIP: <All>,<Explicit>, <Transparent> or an ip 
		portRange: 80, 8080, etc 
		action: bypass or intercept	 

		Returns: True or raise error
		'''

		self.command ("proxy-services", context='CLI_CONFIG')
		self.command ('edit "{}"'.format (service) )
		retVal = self.command ('{} {} {}'.format (action, destinationIP, portRange) )
		
		if retVal.find("ok") == -1: raise Error (retVal)
		return True

	# --------------------------------------------------------------------------

	def getServiceAction (self, service, destinationIP, portRange): #need combination of dip and port as it allows duplicate ip
		'''View service action settings
		destinationIP: IP of the server
		portRange: portrange of the service
		Returns: String - action (Bypass or Intercept)
		'''

		actionList = self.viewAction (service)
		retValue = []
		for action in actionList:
			if action[0] == destinationIP:
				if action[1] == portRange:
					retValue = action[2]
					break
		return retValue
 
	# --------------------------------------------------------------------------

	def addProxyService (self, serviceType, destIp, portRange, action):
		'''Add a proxy service
		serviceType: HTTP, FTP, SSH, etc. 
		destIp: all, transparent, explicit, 192.168.20.1
		portRange: 80, 21, etc. 
		action: intercept, bypass
		Returns: True for success; Raise error on failure
		'''

		# -- Add the listener
		self.command ("proxy-services", context='CLI_CONFIG')
		retVal = self.command ('edit "{}"'.format (serviceType))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)

		retVal = self.command ('add {} {} {}'.format (destIp, portRange, action) )
		if retVal.find("ok") >= 0: 
			return True 

		# -- Error found, remove duplicates and add again

		if re.search ("Error due to conflict in the following listeners", retVal, re.I):
			# -- Get the conflicts, parse into array ( (sourceIP, destinationIP, portRange, serviceType), ...)
			actionList = re.findall(r"(?mi)^\s*listener '(\S+) -> ([^:]+):([^']+)' on proxy service '([^']+)'", retVal)
			autotest.log('debug', "\n----CONFLICT!!! actionList: "+str(actionList)+"\n\n")
			del actionList[0] # 1st item is the request itself

			# -- Remove the conflicts
			for sIp, dIp, pRange, sType in actionList:
				sType = sType.lower()
				# -- Service type the same as the requested one
				if sType != serviceType.lower():
					self.command("proxy-services", context='CLI_CONFIG')
					self.command('edit "{}"'.format(sType))
				retVal = self.command("remove {} {} {}".format(sIp, dIp, pRange))

			# Add the requested service
			self.command ("proxy-services", context='CLI_CONFIG')
			retVal = self.command ('edit "{}"'.format(serviceType))
			if re.search ('^% ',retVal,re.M): raise Error (retVal)
			retVal = self.command ('add {} {} {}'.format (destIp, portRange, action) )
			if retVal.find("ok") >= 0: 
				return True 
			raise Error ('addProxyService failed on second add attempt: '+retVal)

	# --------------------------------------------------------------------------

	def removeproxyService (self, serviceType, destIp, portRange):
		'''Remove a proxy service
		
		serviceType: HTTP, FTP, SSH, etc.
		destIp: all, transparent, explicit, 192.168.20.1
		portRange: 80, 21, etc.
		Returns: True for success; False for failure'''

		self.command ("proxy-services", context='CLI_CONFIG')
		retVal = self.command ('edit "{}"'.format(serviceType))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		retVal = self.command('remove {} {}'.format(destIp, portRange) )
		self.command ("exit")  # http
		self.command ("exit")  # proxy-service
		if retVal.find("No matching listener found in service") >= 0 or retVal.find("ok") >= 0:
			return True
		else:	   
			return False 
	
	# --------------------------------------------------------------------------

	def setProxyServiceAttr (self, serviceType, attributes):
		'''Add a proxy service
		serviceType: HTTP, FTP, SSH, etc. 
		attributes: <attr_name>:<enable|disable> dic. pair 
				adn-byte-cache               Enable or disable ADN byte caching
 				adn-compress                 Enable or disable ADN compression
 				adn-thin-client              Enable or disable ADN thin client processing
 				byte-cache-priority          Adjust retention priority of byte cache data
 				detect-protocol              Enable or disable protocol detection
 				early-intercept              Enable or disable early interception
 				use-adn                      Enable or disable ADN
		Returns: True for success; False for failure
		'''

		availAttr = [
				'adn-byte-cache',
 				'adn-compress',
 				'adn-thin-client',
 				'byte-cache-priority',
 				'detect-protocol',    
 				'early-intercept',    
 				'use-adn'
 				]
		self.command ("proxy-services", context='CLI_CONFIG')
		retVal = self.command ('edit "{}"'.format(serviceType))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		errorCount = 0
		for attr in attributes.keys():
			if attr not in availAttr:
				raise Error ("{} attribute is not available for the {} service".format(attr, serviceType))
			if attributes[attr] not in ('disable', 'enable'):
				raise Error ("{} attribute value must be either 'enable' or 'disable' for the {} service".format(attr, serviceType))
			retVal = self.command ('attribute {} {}'.format(attr,attributes[attr]))
			if retVal.find("ok") >= 0:
				errorCount += 1
		return errorCount == 0
			
	# --------------------------------------------------------------------------

	def getNumberOfHttpConnections (self):
		'''Get number of HTTP connections from  'show http-stats' output
		Returns: number of http connections
		'''
		
		retVal=self.command ("show http-stats", context='CLI_ENABLE') 
		alist = retVal.split("\n")
		for i in range(0, len(alist)):
			autotest.log('internal'," alist[i]:"+str(alist[i]))
			if (alist[i].find("Connections accepted") >= 0):
			#if (alist[i].find("Currently established client connections") >= 0):
				noHttpConnections = (alist[i].split(":"))[1].strip()
				autotest.log('debug', "noHttpConnections: "+noHttpConnections)
				break
		return noHttpConnections 
		
	# --------------------------------------------------------------------------

	def createProxyService (self, proxyType, proxyName):
		'''Create a new proxy service
		proxyType: http, ftp, etc
		proxyName: the name of the proxy to be created 
		Returns: True or False
		'''

		self.command("proxy-services", context='CLI_CONFIG')
		retVal = self.command('create {} "{}"'.format (proxyType, proxyName) )
		return retVal.find("ok") >= 0

	# --------------------------------------------------------------------------

	def deleteProxyService (self, proxyName):
		'''Delete a proxy service
		proxyName: "External http", ftp, etc
		Returns: True or False
		'''
	
		self.command("proxy-services", context='CLI_CONFIG')
		retVal = self.command('delete "{}"'.format(proxyName) )
		return retVal.find("ok") >= 0

	# --------------------------------------------------------------------------

	def editProxyType(self, proxyType, proxyName):
		'''Changes the proxy type of a given service
		proxyType: http, ftp, etc
		proxyName: the name of the proxy whose type needs to be changed
		Returns: True or False
		'''

		self.command ("proxy-services", context='CLI_CONFIG')
		retVal = self.command ('edit "{}"'.format (proxyName))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		retVal = self.command('proxy-type "{}"'.format (proxyType) )
		return retVal.find("ok") >= 0

	# --------------------------------------------------------------------------

	def editProxyAttributes(self, proxyName, sourceIp, destIp, portRange, action):
		'''Change the attributes of a given service
		proxyName: the name of the proxy whose type needs to be changed 
		sourceIp:  all or an ip/ip range
		destIp:	all or an ip/ip range 
		portRange: e.g. 21 for ftp
		action:	intercept or bypass
		Returns: True or False
		'''

		self.command ("proxy-services", context='CLI_CONFIG')
		retVal = self.command ('edit "{}"'.format (proxyName))
		if re.search ('^% ',retVal,re.M): raise Error (retVal)
		retVal = self.command('{} {} {} {}'.format (action, sourceIp, destIp, portRange))
		if retVal.find("ok") >= 0: return True

		autotest.log ('debug', "***ERROR: Failed to edit " +proxyName+ "proxy attributes.")
		autotest.log ('debug', "editProxyAttributes() returned: " +retVal)
		return False

	# --------------------------------------------------------------------------

	def setProxyDefault (self, default = "allow"):
		'''Set policy proxy-default to allow or deny
		 default: allow or deny
		 Returns: True or False
		'''
		#self.setConfigPrompt()
		retVal = self.command("policy proxy-default " + default, context="CLI_CONFIG")
		if retVal.find("ok") >= 0: return True
		
		autotest.log ('error', "***ERROR on setting 'policy proxy-default' to "+default)
		return False


	# --------------------------------------------------------------------------

	def getPeerId(self):
		'''Get the Peer ID of the ProxySG appliance (serial number)
		
		Returns: peerID of the proxy appliance
		'''

		retVal = self.command("show version", context='CLI_ENABLE')
		match = re.search ('(?mi)^Serial number:\s+(\d+)', retVal)
		if match:
			self.peerId = match.group(1)
		return self.peerId
		
	# --------------------------------------------------------------------------

	def setRejectInbound(self, interfaceId, mode='disable'):
		'''Set reject-inbound to enable or disable for a given interface
		 
		interfaceId: SG's interface label (0:0, 1:0, etc.)
 		mode: enable or disable (default)
		Returns: True or False
		'''

		self.command ('interface {}'.format (interfaceId), context='CLI_CONFIG')
		retVal = self.command('reject-inbound {}'.format (mode))
		return retVal.find("ok") >= 0
	
	# --------------------------------------------------------------------------

	def setForceBypass(self, mode='disable'):
		'''Set force-bypass proxy services to enable or disable

 		mode: enable or disable (default)
		Returns: True or False
		'''

		self.command("proxy-services", context='CLI_CONFIG')
		retVal = self.command ('force-bypass {}'.format (mode))
		return retVal.find("ok") >= 0
