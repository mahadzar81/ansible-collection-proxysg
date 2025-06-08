import re
import os
import sys
import autotest
import fileinput

from ansible.module_utils.urls import fetch_url
from ansible.module_utils._text import to_bytes, to_native
from ansible.module_utils.basic import env_fallback
from ansible.module_utils.connection import Connection, ConnectionError


class Error(Exception):
    def __init__(self, value):
        self.value = value

    def __str__(self):
        return repr(self.value)


class Config:
    EOF_MARKER = "EOF1234"

    def __init__(self, sgcli):
        self.sgcli = sgcli
        self.command = sgcli.command

    def _read_file(self, file_path: str) -> str:
        """
        Reads the contents of a file and returns it as a string.
        """
        content = ''
        try:
            with open(file_path, 'r') as f:
                content = f.read()
        except Exception as e:
            autotest.log('error', f"Failed to read file {file_path}: {e}")
            raise
        return content

    def _execute_command(self, command: str) -> str:
        """
        Executes a CLI command on the ProxySG device.
        Returns the output from the device.
        """
        autotest.log('debug', f"Issuing SG CLI command: {command}")
        output = self.command(command)
        autotest.log('debug', f"SG Response: {output}")
        return output

    def _check_response_ok(self, response: str) -> bool:
        """
        Checks if the response contains 'ok' indicating success.
        """
        return bool(re.search(r'ok', response))

    def create_keyring(
        self,
        keyring_name: str,
        show_status: str,
        cert_path: str,
        key_path: str,
        key_passphrase: str = None
    ) -> bool:
        """
        Creates an SSL keyring using the provided certificate and private key files.
        Returns True on success, False otherwise.
        """
        self._execute_command("ssl", context='CLI_CONFIG')

        autotest.log('info', f"Reading private key from {key_path}")
        private_key_content = self._read_file(key_path)
        autotest.log('debug', f"Private Key Content:\n{private_key_content}")

        autotest.log('info', f"Reading certificate from {cert_path}")
        certificate_content = self._read_file(cert_path)
        autotest.log('debug', f"Certificate Content:\n{certificate_content}")

        # Create keyring with private key
        autotest.log('info', f"Creating SSL keyring: {keyring_name}")
        if key_passphrase:
            cmd = f"inline keyring {show_status} {keyring_name} {key_passphrase} {self.EOF_MARKER}\n{private_key_content}\n{self.EOF_MARKER}"
        else:
            cmd = f"inline keyring {show_status} {keyring_name} {self.EOF_MARKER}\n{private_key_content}\n{self.EOF_MARKER}"

        response = self._execute_command(cmd)
        if not self._check_response_ok(response):
            autotest.log('info', "Failed to import private key.")
            return False

        # Import certificate
        autotest.log('info', f"Importing certificate into keyring: {keyring_name}")
        cert_cmd = f"inline certificate {keyring_name} {self.EOF_MARKER}\n{certificate_content}\n{self.EOF_MARKER}"
        response = self._execute_command(cert_cmd)

        if not self._check_response_ok(response):
            autotest.log('info', "Failed to import certificate.")
            return False

        return True

    def delete_keyring(self, keyring_name: str, mute_errors: bool = False) -> bool:
        """
        Deletes an existing SSL keyring.
        Returns True on success, False otherwise.
        """
        self._execute_command("ssl", context='CLI_CONFIG')
        response = self._execute_command(f"delete keyring {keyring_name}")
        if not self._check_response_ok(response):
            if mute_errors:
                return True
            autotest.log('info', f"Failed to delete keyring: {keyring_name}")
            return False
        return True

    def set_issuer_keyring(self, keyring_name: str = 'default') -> bool:
        """
        Sets the proxy issuer keyring.
        Returns True on success, False otherwise.
        """
        self._execute_command("ssl", context='CLI_CONFIG')
        response = self._execute_command(f"proxy issuer-keyring {keyring_name}")
        if not self._check_response_ok(response):
            autotest.log('info', "Failed to set issuer keyring.")
            return False
        return True

    def clear_server_certificate_cache(self) -> bool:
        """Clears the server certificate cache."""
        self._execute_command("ssl", context='CLI_CONFIG')
        response = self._execute_command("clear-certificate-cache")
        if not self._check_response_ok(response):
            autotest.log('info', "Failed to clear server certificate cache.")
            return False
        return True

    def clear_session_cache(self) -> bool:
        """Clears the SSL session cache."""
        self._execute_command("ssl", context='CLI_CONFIG')
        response = self._execute_command("clear-session-cache")
        if not self._check_response_ok(response):
            autotest.log('info', "Failed to clear SSL session cache.")
            return False
        return True

    def import_ca_certificate(self, ca_cert_name: str, ca_cert_path: str) -> None:
        """Imports a CA certificate."""
        self._execute_command("ssl", context='CLI_CONFIG')
        cert_content = self._read_file(ca_cert_path)
        autotest.log('info', f"Importing CA Certificate: {ca_cert_name}")
        cmd = f"inline ca-certificate {ca_cert_name} {self.EOF_MARKER}\n{cert_content}\n{self.EOF_MARKER}"
        self._execute_command(cmd)

    def delete_ca_certificate(self, ca_cert_name: str, fail_on_error: bool = True) -> None:
        """Deletes a CA certificate."""
        self._execute_command("ssl", context='CLI_CONFIG')
        self._execute_command(f"delete ca-certificate {ca_cert_name}")

    def add_ca_to_ccl(self, ca_cert_name: str, ccl_name: str) -> None:
        """Adds a CA certificate to a CCL."""
        self._execute_command("ssl", context='CLI_CONFIG')
        self._execute_command(f"edit ccl {ccl_name}")
        self._execute_command(f"add {ca_cert_name}")
        self._execute_command("exit")

    def remove_ca_from_ccl(self, ca_cert_name: str, ccl_name: str) -> None:
        """Removes a CA certificate from a CCL."""
        self._execute_command("ssl", context='CLI_CONFIG')
        self._execute_command(f"edit ccl {ccl_name}")
        self._execute_command(f"remove {ca_cert_name}")
        self._execute_command("exit")

    def add_crl(self, crl_name: str, crl_path: str) -> None:
        """Imports a CRL."""
        self._execute_command("ssl", context='CLI_CONFIG')
        crl_content = self._read_file(crl_path)
        self._execute_command(f"create crl {crl_name}")
        cmd = f"inline crl {crl_name} {self.EOF_MARKER}\n{crl_content}\n{self.EOF_MARKER}"
        self._execute_command(cmd)

    def delete_crl(self, crl_name: str) -> None:
        """Deletes a CRL."""
        self._execute_command("ssl", context='CLI_CONFIG')
        self._execute_command(f"delete crl {crl_name}")