'''Helper routines for ProxySG SSL features/configuration'''

__author__ = 'Maza'
__version__ = '1.0'

#=====================================================================================
#=====================================================================================

import re
import os, sys
import autotest
import fileinput

from ansible.module_utils.urls import fetch_url
from ansible.module_utils._text import to_bytes, to_native
from ansible.module_utils.basic import env_fallback
from ansible.module_utils.connection import Connection, ConnectionError


class Error (Exception):
   def __init__ (self, value):
      self.value = value
   def __str__ (self):
      return repr (self.value)


class config:

   def __init__ (self, sgcli):
      self.sgcli = sgcli
      self.command = sgcli.command

   def _readFile(self,file_path):
      '''
      Reads the contents of a file and returns that content in a string.
      '''
      myfile_content = ''
      for myline in fileinput.input(file_path):
         myfile_content += myline
      return myfile_content


   def createKeyring(self, keyringName, showStatus, certPath, keyPath, keyPassphrase=None):
      '''
      Creates an SSL keyring on the ProxySG using  a specified certificate
      private key and private key password if encrypted.
      
      keyringName   : Specify the name of the SSL keyring to create
      showStatus    : Expect 'show', 'no-show', 'show-director'
      certPath      : Specify the path to the certificate file including the filename.  
                      This should be PEM formatted text file residing on the local system
      keyPath       : Specify the path to the private key file including the filename.  
                      This should be PEM formatted text file residing on the local system
      keyPassphrase : Specify the passphrase to decrypt the private key (if this
                      key is encrypted.)  Defaults to 'None'

      Returns
         True  : All operations completed successfuly and the keyring has been created
         False : Hit an error when attempting to create the keyring.  Debug output may contain 
                 more info

      '''

      self.command("ssl", context='CLI_CONFIG') 

      # TODO VALIDATE showStatus     

      # Attempt to read the content of the specified private key file
      autotest.log('info','Attempting to grab the private key content from '+keyPath)
      privateKeyContent = config._readFile(self,keyPath)
      autotest.log('debug','Obtained private key content\n####START OF PRIVATE KEY CONTENT####\n'+privateKeyContent+'\n####END OF PRIVATE KEY CONTENT####')

      # Attempt to read the content of the specified certificate file
      autotest.log('info','Attempting to grab the certificate content from '+certPath)
      certificateContent = config._readFile(self,certPath)
      autotest.log('debug','Obtained certificate content\n####START OF CERTIFICATE CONTENT####\n'+certificateContent+'\n####END OF CERTIFICATE CONTENT####')

      # Now let's create the keyring and insert the private key
      if keyPassphrase != None:
         autotest.log('info','Creating new SSL keyring: '+keyringName)
         autotest.log('debug','Issuing SG CLI command: inline keyring '+showStatus+' '+keyringName+' '+keyPassphrase+' EOF1234 .. <PRIVATE KEY CONTENT> .. EOF1234')
         cmdOutput = self.command('inline keyring '+showStatus+' '+keyringName+' '+keyPassphrase+' EOF1234\n'+privateKeyContent+'\nEOF1234')
         autotest.log('debug','SG Response: '+cmdOutput)
      else:
         autotest.log('info','Creating new SSL keyring: '+keyringName)
         autotest.log('debug','Issuing SG CLI command: inline keyring '+showStatus+' '+keyringName+' EOF1234 .. <PRIVATE KEY CONTENT> .. EOF1234')
         cmdOutput = self.command('inline keyring '+showStatus+' '+keyringName+' EOF1234\n'+privateKeyContent+'\nEOF1234')
         autotest.log('debug','SG Response: '+cmdOutput)

      # Let's check to ensure we were able to import the private key correctly into our new keyring
      check = re.search('ok', cmdOutput)
      if check:
         autotest.log('debug', 'Private key imported correctly')
      else:
         autotest.log('info', 'Hit an error when attempting to create a new keyring.  Failure when importing private key!')
         return False     

      # Assuming the private key imported correctly we can now move on to import the certificate
      autotest.log('info','Importing certificate into SSL keyring: '+keyringName)
      autotest.log('debug','Issuing SG CLI command: inline certificate '+keyringName+' EOF1234 .. <CERTIFICATE CONTENT> .. EOF1234')
      cmdOutput = self.command('inline certificate '+keyringName+' EOF1234\n'+certificateContent+'\nEOF1234')
      autotest.log('debug','SG Response: '+cmdOutput)

      # Let's check to ensure we were able to import the certificate correctly
      check = re.search('ok', cmdOutput)
      if check:
         autotest.log('debug', 'Certificate imported correctly')
         return True
      else:
         autotest.log('info', 'Hit an error when attempting to create a new keyring.  Failure when importing certificate!')
         return False



   def deleteKeyring(self, keyringName, muteErrors=False):
      '''
      Deletes an existing ProxySG SSL keyring.
  
      Note 
      - This will only work if the keyring is not currently in use!
      '''
      
      self.command("ssl", context='CLI_CONFIG')
      autotest.log('info','Deleting SSL Keyring - '+keyringName)
      autotest.log('debug','Issued SG command: delete keyring '+keyringName)
      cmdOutput = self.command("delete keyring "+keyringName)
      autotest.log('debug','Response: '+cmdOutput)

      # Check the response for an ok, or potential error
      check = re.search('ok', cmdOutput)
      if check:
         autotest.log('debug', 'Keyring deleted successfully')
         return True
      else:
         if muteErrors:
            return True
         else:
            autotest.log('debug', 'Hit an error when attempting to delete the SSL keyring: '+keyringName)
            return False
      

   def setIssuerKeyring(self, keyringName='default'):
      '''
      Sets the proxy issuer keyring to the specific keyringName.  
      If none is provided then it resets the issuer keyring back to 'default'

      Returns: True on successful outcome, False if an issue is encountered
      
      Notes 
      - This will only work if the keyring exists!
      - Policy overrides 'proxy issuer-keyring XXX' specification so if there
        is any policy set to specify the issuer keyring it supercedes the
        configuration generated by this command
      '''
      
      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Setting ProxySG Issuer Keyring to - '+keyringName)
      autotest.log('debug','Issued SG command: proxy issuer-keyring '+keyringName)
      cmdOutput = self.command('proxy issuer-keyring '+keyringName)
      autotest.log('debug','Response: '+cmdOutput)
      
      # Check the response for an ok, or potential error
      check = re.search('ok', cmdOutput)
      if check:
         autotest.log('debug', 'ProxySG issuer keyring set successfully')
         return True
      else: 
         autotest.log('info', 'Hit an error when attempting to set the ProxySG issuer keyring!')
         return False

   def clearServerCertificateCache(self):
      '''
      Clears the SG Server certificate cache
      '''

      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Clearing SG Server Certificate Cache')
      cmdOutput = self.command('clear-certificate-cache')

      # Check the response for an ok, or potential error
      check = re.search('ok', cmdOutput)
      if check:
         autotest.log('debug', 'SG Server Certificate Cache cleared successfully')
         return True
      else:
         autotest.log('info', 'Did not receive an OK response to clearing the SG Server Certificate Cache!')
         return False

   def clearSessionCache(self):
      '''
      Clears the SG's SSL session cache
      '''

      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Clearing SG SSL Session Cache')
      cmdOutput = self.command('clear-session-cache')

      # Check the response for an ok, or potential error
      check = re.search('ok', cmdOutput)
      if check:
         autotest.log('debug', 'SG SSL Session Cache cleared successfully')
         return True
      else:
         autotest.log('info', 'Did not receive an OK response to clearing the SG SSL Session Cache!')
         return False

   def importCACertificate(self, caCertName, caCertPath):
      '''
      Imports a CA certificate from a specified file and associates the given name.

      Note: Assumes the specified file contains a correctly formatted CA certificate (in PEM format)
      '''

      # TODO: Sort out proper error detection and return status

      self.command("ssl", context='CLI_CONFIG')

      # Attempt to read the content of the specified ca certificate file
      autotest.log('info','Attempting to grab the CA certificate content from '+caCertPath)
      certificateContent = config._readFile(self,caCertPath)
      autotest.log('debug','Obtained CA certificate content\n####START OF CERTIFICATE CONTENT####\n'+certificateContent+'\n####END OF CERTIFICATE CONTENT####')

      # Now let's import the CA certificate
      autotest.log('info','Importing CA Certificate: '+caCertName)
      autotest.log('debug','Issuing SG CLI command: inline ca-certificate '+caCertName+' EOF1234 .. <CA CERT CONTENT> .. EOF1234')
      cmdOutput = self.command('inline ca-certificate '+caCertName+' EOF1234\n'+certificateContent+'\nEOF1234')
      autotest.log('debug','SG Response: '+cmdOutput)

   def deleteCACertificate(self, caCertName, failOnError = True):
      '''
      Delete's a CA Certificate from the SG using the specified caCertName
      '''

      # TODO: Sort out proper error detection and return status

      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Deleting CA Certificate: '+caCertName)
      autotest.log('debug','Issuing SG CLI command: delete ca-certificate '+caCertName)
      cmdOutput = self.command('delete ca-certificate '+caCertName)
      autotest.log('debug','SG Response: '+cmdOutput)

   def addCACertificateToCCL(self, caCertName, cclName):
      '''
      Adds a specified CA certificate to the specified CCL

      Note:  
      - The CA certificate must have already been imported to the SG
      - The CCL must already exist on the SG
      '''

      # TODO: Sort out proper error detection and return status

      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Adding CA Certificate '+caCertName+' to CCL '+cclName)
      autotest.log('debug','Issuing SG CLI command: edit ccl '+cclName)
      cmdOutput = self.command('edit ccl '+cclName)
      autotest.log('debug','Issuing SG CLI command: add '+cclName)
      cmdOutput = self.command('add '+caCertName)
      autotest.log('debug','SG Response: '+cmdOutput)
      cmdOutput = self.command('exit')
       

   def deleteCACertificateFromCCL(self, caCertName, cclName):
      '''
      Removes a specified CA certificate from the specified CCL

      Note:
      - The CA certificate must have already been imported to the SG
      - The CCL must already exist on the SG and contain the aforementioned CA certificate
      '''

      # TODO: Sort out proper error detection and return status

      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Removing CA Certificate '+caCertName+' from CCL '+cclName)
      autotest.log('debug','Issuing SG CLI command: edit ccl '+cclName)
      cmdOutput = self.command('edit ccl '+cclName)
      autotest.log('debug','Issuing SG CLI command: remove '+cclName)
      cmdOutput = self.command('remove '+caCertName)
      autotest.log('debug','SG Response: '+cmdOutput)
      cmdOutput = self.command('exit')

   def addCRL(self, crlName, crlFilePath):
      '''
      Imports a CRL (Certificate Revocation List) from a specified file and associates the given name.

      Note: Assumes the specified file contains a correctly formatted CRL (in PEM format)
      '''

      # TODO: Sort out proper error detection and return status

      self.command("ssl", context='CLI_CONFIG')

      # Attempt to read the content of the specified crl file
      autotest.log('info','Attempting to grab the CRL content from '+crlFilePath)
      crlContent = config._readFile(self,crlFilePath)
      autotest.log('debug','Obtained CRL content\n####START OF CRL CONTENT####\n'+crlContent+'\n####END OF CRL CONTENT####')

      # Creating the CRL
      autotest.log('info','Creating CRL: '+crlName)
      cmdOutput = self.command('create crl '+crlName)
      autotest.log('debug','SG Response: '+cmdOutput)

      # Now let's import the CRL
      autotest.log('debug','Importing CRL content ... Issuing SG CLI command: inline crl '+crlName+' EOF1234 .. <CRL CONTENT> .. EOF1234')
      cmdOutput = self.command('inline crl '+crlName+' EOF1234\n'+crlContent+'\nEOF1234')
      autotest.log('debug','SG Response: '+cmdOutput) 

   def deleteCrl(self, crlName):
      '''
      Delete's a CRL (Certificate Revokation List) from the SG using the specified crlName
      '''

      # TODO: Sort out proper error detection and return status

      self.command("ssl", context='CLI_CONFIG')

      autotest.log('info','Deleting CRL: '+crlName)
      autotest.log('debug','Issuing SG CLI command: delete crl '+crlName)
      cmdOutput = self.command('delete crl '+crlName)
      autotest.log('debug','SG Response: '+cmdOutput)


