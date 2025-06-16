'''
ProxySg proxy services library
'''
__author__ = 'Maza'
__version__ = '1.0.1'

import re
import os
import sys
import autotest


class Error(Exception):
    pass


class SgProxyServices:
    '''
    Example (stand alone):
    sg1 = proxysg.ProxySGCLI('proxysg_1')
    services1 = sgProxyServices.SgProxyServices(sg1)
    print(services1.viewProxyServices('cifs'))
    
    Example (aggregate with proxysg):
    class TestCLI(proxysg.ProxySGCLI, sgProxyServices.SGProxyServices): pass
    sg1 = TestCLI('proxysg_1')
    print(sg1.viewProxyServices('cifs'))
    '''

    serviceConfigRE = re.compile(r"(?im)(\w.+\:)\s+(.+)\s*$")
    serviceActionRE3 = re.compile(r"(?im)((?:<\w+>)|(?:\d+\.\d+\.\d+\.\d+)|(?:\d+\.\d+\.\d+\.\d+/\d+))\s+(\d+)\s+(\w+)\s*$")

    def __init__(self, sgcli):
        '''link SG command routine'''
        self.sgcli = sgcli
        self.command = sgcli.command

    def editProxyServices(self, service):
        '''Enter Edit mode of a given service
        service - a proxy service name 
        Returns: true or raise error
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{service}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        return True

    def viewProxyServices(self, service):
        '''View service settings
        service - a proxy service name e.g http, ftp
        Returns:  List of tuple - [('Service Name:','value'),('Service Group:','value'),
                                    ('Proxy:','value'),('Attributes:','value')]
        '''
        serviceList = []
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command("edit " + service)
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        retVal = self.command("view")
        serviceList = self.serviceConfigRE.findall(retVal)
        return serviceList

    def viewProxyServiceAction(self, service):
        '''View service action settings
        service - service to view
        Returns: List of tuple - e.g. [('<All>', '80', 'Bypass'), ('<Explicit>', '8080', 'Bypass')]
        '''
        actionList = []
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{service}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        retVal = self.command("view")
        actionList = self.serviceActionRE3.findall(retVal)
        return actionList

    def editServiceAction(self, service, destinationIP, portRange, action):
        '''Edit a service's action, destinationIP and portRange
        service: http, ftp, etc
        destinationIP: <All>,<Explicit>, <Transparent> or an ip 
        portRange: 80, 8080, etc 
        action: bypass or intercept	 
        Returns: True or raise error
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        self.command(f'edit "{service}"')
        retVal = self.command(f'{action} {destinationIP} {portRange}')
        if "ok" not in retVal:
            raise Error(retVal)
        return True

    def getServiceAction(self, service, destinationIP, portRange):  # need combination of dip and port as it allows duplicate ip
        '''View service action settings
        destinationIP: IP of the server
        portRange: portrange of the service
        Returns: String - action (Bypass or Intercept)
        '''
        actionList = self.viewProxyServiceAction(service)
        retValue = []
        for act in actionList:
            if act[0] == destinationIP and act[1] == portRange:
                retValue = act[2]
                break
        return retValue

    def addProxyService(self, serviceType, destIp, portRange, action):
        '''Add a proxy service
        serviceType: HTTP, FTP, SSH, etc. 
        destIp: all, transparent, explicit, 192.168.20.1
        portRange: 80, 21, etc. 
        action: intercept, bypass
        Returns: True for success; Raise error on failure
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{serviceType}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        retVal = self.command(f'add {destIp} {portRange} {action}')
        if "ok" in retVal:
            return True
        if re.search(r"Error due to conflict in the following listeners", retVal, re.I):
            actionList = re.findall(
                r"(?mi)^\s*listener '(\S+) -> ([^:]+):([^']+)' on proxy service '([^']+)'",
                retVal
            )
            autotest.log('debug', "\n----CONFLICT!!! actionList: " + str(actionList))
            del actionList[0]  # First item is the request itself
            for sIp, dIp, pRange, sType in actionList:
                sType = sType.lower()
                if sType != serviceType.lower():
                    self.command("proxy-services", context='CLI_CONFIG')
                    self.command(f'edit "{sType}"')
                retVal = self.command(f'remove {sIp} {dIp} {pRange}')
            self.command("proxy-services", context='CLI_CONFIG')
            retVal = self.command(f'edit "{serviceType}"')
            if re.search(r'^% ', retVal, re.M):
                raise Error(retVal)
            retVal = self.command(f'add {destIp} {portRange} {action}')
            if "ok" in retVal:
                return True
            raise Error('addProxyService failed on second add attempt: ' + retVal)

    def removeproxyService(self, serviceType, destIp, portRange):
        '''Remove a proxy service
        serviceType: HTTP, FTP, SSH, etc.
        destIp: all, transparent, explicit, 192.168.20.1
        portRange: 80, 21, etc.
        Returns: True for success; False for failure'''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{serviceType}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        retVal = self.command(f'remove {destIp} {portRange}')
        self.command("exit")  # http
        self.command("exit")  # proxy-service
        if "No matching listener found in service" in retVal or "ok" in retVal:
            return True
        else:
            return False

    def setProxyServiceAttr(self, serviceType, attributes):
        '''Add a proxy service
        serviceType: HTTP, FTP, SSH, etc. 
        attributes: <attr_name>:<enable|disable> dict pair 
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
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{serviceType}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        errorCount = 0
        for attr in attributes.keys():
            if attr not in availAttr:
                raise Error(f"{attr} attribute is not available for the {serviceType} service")
            if attributes[attr] not in ('disable', 'enable'):
                raise Error(f"{attr} attribute value must be either 'enable' or 'disable' for the {serviceType} service")
            retVal = self.command(f'attribute {attr} {attributes[attr]}')
            if "ok" in retVal:
                errorCount += 1
        return errorCount == 0

    def getNumberOfHttpConnections(self):
        '''Get number of HTTP connections from  'show http-stats' output
        Returns: number of http connections
        '''
        retVal = self.command("show http-stats", context='CLI_ENABLE')
        alist = retVal.split("\n")
        for line in alist:
            if "Connections accepted" in line:
                noHttpConnections = line.split(":")[1].strip()
                autotest.log('debug', "noHttpConnections: " + noHttpConnections)
                break
        return noHttpConnections

    def createProxyService(self, proxyType, proxyName):
        '''Create a new proxy service
        proxyType: http, ftp, etc
        proxyName: the name of the proxy to be created 
        Returns: True or False
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'create {proxyType} "{proxyName}"')
        return "ok" in retVal

    def deleteProxyService(self, proxyName):
        '''Delete a proxy service
        proxyName: "External http", ftp, etc
        Returns: True or False
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'delete "{proxyName}"')
        return "ok" in retVal

    def editProxyType(self, proxyType, proxyName):
        '''Changes the proxy type of a given service
        proxyType: http, ftp, etc
        proxyName: the name of the proxy whose type needs to be changed
        Returns: True or False
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{proxyName}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        retVal = self.command(f'proxy-type "{proxyType}"')
        return "ok" in retVal

    def editProxyAttributes(self, proxyName, sourceIp, destIp, portRange, action):
        '''Change the attributes of a given service
        proxyName: the name of the proxy whose type needs to be changed 
        sourceIp:  all or an ip/ip range
        destIp:	all or an ip/ip range 
        portRange: e.g. 21 for ftp
        action:	intercept or bypass
        Returns: True or False
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'edit "{proxyName}"')
        if re.search(r'^% ', retVal, re.M):
            raise Error(retVal)
        retVal = self.command(f'{action} {sourceIp} {destIp} {portRange}')
        if "ok" in retVal:
            return True
        autotest.log('debug', "***ERROR: Failed to edit " + proxyName + "proxy attributes.")
        autotest.log('debug', "editProxyAttributes() returned: " + retVal)
        return False

    def setProxyDefault(self, default="allow"):
        '''Set policy proxy-default to allow or deny
         default: allow or deny
         Returns: True or False
        '''
        retVal = self.command("policy proxy-default " + default, context="CLI_CONFIG")
        if "ok" in retVal:
            return True
        autotest.log('error', "***ERROR on setting 'policy proxy-default' to " + default)
        return False

    def getPeerId(self):
        '''Get the Peer ID of the ProxySG appliance (serial number)
        Returns: peerID of the proxy appliance
        '''
        retVal = self.command("show version", context='CLI_ENABLE')
        match = re.search(r'(?mi)^Serial number:\s+(\d+)', retVal)
        if match:
            self.peerId = match.group(1)
        return self.peerId

    def setRejectInbound(self, interfaceId, mode='disable'):
        '''Set reject-inbound to enable or disable for a given interface
        interfaceId: SG's interface label (0:0, 1:0, etc.)
        mode: enable or disable (default)
        Returns: True or False
        '''
        self.command(f'interface {interfaceId}', context='CLI_CONFIG')
        retVal = self.command(f'reject-inbound {mode}')
        return "ok" in retVal

    def setForceBypass(self, mode='disable'):
        '''Set force-bypass proxy services to enable or disable
        mode: enable or disable (default)
        Returns: True or False
        '''
        self.command("proxy-services", context='CLI_CONFIG')
        retVal = self.command(f'force-bypass {mode}')
        return "ok" in retVal