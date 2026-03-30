"""Configuration file encryption using AES-GCM.

Adapted from CAT config/configManager.py encrypt_file() / decrypt_file().
Uses pycryptodomex (Cryptodome) for AES-GCM encryption with PBKDF2 key derivation.
Gracefully degrades when pycryptodomex is not installed.
"""

import os
import json
import logging
from typing import Optional, Dict, Any, Tuple

logger = logging.getLogger(__name__)

# Try to import pycryptodomex
try:
    from Cryptodome.Cipher import AES
    from Cryptodome.Protocol.KDF import PBKDF2
    from Cryptodome.Random import get_random_bytes
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False
    logger.info("pycryptodomex not available — config encryption disabled")


# Default iteration count for PBKDF2
_PBKDF2_ITERATIONS = 1_000_000
_KEY_LENGTH = 32  # 256-bit key
_SALT_LENGTH = 32


class ConfigEncryption:
    """Encrypts and decrypts configuration files using AES-256-GCM.

    Mirrors CAT configManager encrypt_file() / decrypt_file().

    Parameters
    ----------
    passphrase : str
        Passphrase for key derivation. Should be stored securely.
    """

    def __init__(self, passphrase: str = ""):
        self._passphrase = passphrase or "bento_default_key"

    @property
    def is_available(self) -> bool:
        """Check if encryption libraries are available."""
        return HAS_CRYPTO

    def encrypt_file(
        self,
        source_path: str,
        encrypted_path: str = "",
        salt_path: str = "",
    ) -> Tuple[bool, str]:
        """Encrypt a file using AES-256-GCM.

        Parameters
        ----------
        source_path : str
            Path to the plaintext file to encrypt.
        encrypted_path : str, optional
            Path for the encrypted output. Defaults to source_path + '.enc'.
        salt_path : str, optional
            Path for the salt file. Defaults to source_path + '.salt'.

        Returns
        -------
        tuple of (bool, str)
            (success, error_message)
        """
        if not HAS_CRYPTO:
            return False, "pycryptodomex not installed — cannot encrypt"

        if not os.path.isfile(source_path):
            return False, f"Source file not found: {source_path}"

        if not encrypted_path:
            encrypted_path = source_path + ".enc"
        if not salt_path:
            salt_path = source_path + ".salt"

        try:
            # Read plaintext
            with open(source_path, 'rb') as f:
                plaintext = f.read()

            # Generate salt and derive key
            salt = get_random_bytes(_SALT_LENGTH)
            key = PBKDF2(
                self._passphrase.encode('utf-8'),
                salt,
                dkLen=_KEY_LENGTH,
                count=_PBKDF2_ITERATIONS,
            )

            # Encrypt with AES-GCM
            cipher = AES.new(key, AES.MODE_GCM)
            ciphertext, tag = cipher.encrypt_and_digest(plaintext)

            # Write encrypted file: nonce (16) + tag (16) + ciphertext
            with open(encrypted_path, 'wb') as f:
                f.write(cipher.nonce)
                f.write(tag)
                f.write(ciphertext)

            # Write salt file
            with open(salt_path, 'wb') as f:
                f.write(salt)

            logger.info(f"Encrypted {source_path} -> {encrypted_path}")
            return True, ""

        except Exception as e:
            error = f"Encryption failed: {e}"
            logger.exception(error)
            return False, error

    def decrypt_file(
        self,
        encrypted_path: str,
        salt_path: str = "",
        output_path: str = "",
    ) -> Tuple[bool, str]:
        """Decrypt an AES-256-GCM encrypted file.

        Parameters
        ----------
        encrypted_path : str
            Path to the encrypted file.
        salt_path : str, optional
            Path to the salt file. Defaults to encrypted_path with .salt extension.
        output_path : str, optional
            Path for decrypted output. If empty, returns data but doesn't write.

        Returns
        -------
        tuple of (bool, str)
            (success, error_message_or_decrypted_text)
            On success, second element is the decrypted text.
            On failure, second element is the error message.
        """
        if not HAS_CRYPTO:
            return False, "pycryptodomex not installed — cannot decrypt"

        if not os.path.isfile(encrypted_path):
            return False, f"Encrypted file not found: {encrypted_path}"

        if not salt_path:
            # Try .salt extension
            base = os.path.splitext(encrypted_path)[0]
            salt_path = base + ".salt"
            if not os.path.isfile(salt_path):
                # Try replacing .enc with .salt
                salt_path = encrypted_path.replace(".enc", ".salt")

        if not os.path.isfile(salt_path):
            return False, f"Salt file not found: {salt_path}"

        try:
            # Read salt
            with open(salt_path, 'rb') as f:
                salt = f.read()

            # Read encrypted file
            with open(encrypted_path, 'rb') as f:
                data = f.read()

            # Extract nonce (16 bytes), tag (16 bytes), ciphertext
            nonce = data[:16]
            tag = data[16:32]
            ciphertext = data[32:]

            # Derive key
            key = PBKDF2(
                self._passphrase.encode('utf-8'),
                salt,
                dkLen=_KEY_LENGTH,
                count=_PBKDF2_ITERATIONS,
            )

            # Decrypt
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            decrypted_text = plaintext.decode('utf-8')

            # Optionally write to file
            if output_path:
                with open(output_path, 'w', encoding='utf-8') as f:
                    f.write(decrypted_text)
                logger.info(f"Decrypted {encrypted_path} -> {output_path}")

            return True, decrypted_text

        except Exception as e:
            error = f"Decryption failed: {e}"
            logger.exception(error)
            return False, error

    def encrypt_json(
        self,
        data: Dict[str, Any],
        encrypted_path: str,
        salt_path: str = "",
    ) -> Tuple[bool, str]:
        """Encrypt a dict as JSON to a file.

        Parameters
        ----------
        data : dict
            Data to encrypt.
        encrypted_path : str
            Path for the encrypted output.
        salt_path : str, optional
            Path for the salt file.

        Returns
        -------
        tuple of (bool, str)
            (success, error_message)
        """
        if not HAS_CRYPTO:
            return False, "pycryptodomex not installed — cannot encrypt"

        # Write JSON to temp file, then encrypt
        temp_path = encrypted_path + ".tmp"
        try:
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2)

            success, error = self.encrypt_file(temp_path, encrypted_path, salt_path)
            return success, error
        finally:
            # Clean up temp file
            if os.path.isfile(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def decrypt_json(
        self,
        encrypted_path: str,
        salt_path: str = "",
    ) -> Tuple[bool, Any]:
        """Decrypt a JSON file.

        Parameters
        ----------
        encrypted_path : str
            Path to the encrypted file.
        salt_path : str, optional
            Path to the salt file.

        Returns
        -------
        tuple of (bool, Any)
            (success, parsed_dict_or_error_message)
        """
        success, result = self.decrypt_file(encrypted_path, salt_path)
        if not success:
            return False, result

        try:
            data = json.loads(result)
            return True, data
        except json.JSONDecodeError as e:
            return False, f"Decrypted content is not valid JSON: {e}"

    def encrypt_value(self, value: str) -> Tuple[bool, str]:
        """Encrypt a single string value and return as hex-encoded string.

        Useful for encrypting individual sensitive values (passwords, tokens)
        without writing to files.

        Parameters
        ----------
        value : str
            Plaintext value to encrypt.

        Returns
        -------
        tuple of (bool, str)
            (success, hex_encoded_encrypted_value_or_error)
        """
        if not HAS_CRYPTO:
            return False, "pycryptodomex not installed"

        try:
            salt = get_random_bytes(_SALT_LENGTH)
            key = PBKDF2(
                self._passphrase.encode('utf-8'),
                salt,
                dkLen=_KEY_LENGTH,
                count=_PBKDF2_ITERATIONS,
            )
            cipher = AES.new(key, AES.MODE_GCM)
            ciphertext, tag = cipher.encrypt_and_digest(value.encode('utf-8'))

            # Encode as hex: salt + nonce + tag + ciphertext
            combined = salt + cipher.nonce + tag + ciphertext
            return True, combined.hex()
        except Exception as e:
            return False, f"Value encryption failed: {e}"

    def decrypt_value(self, hex_value: str) -> Tuple[bool, str]:
        """Decrypt a hex-encoded encrypted value.

        Parameters
        ----------
        hex_value : str
            Hex-encoded encrypted value from encrypt_value().

        Returns
        -------
        tuple of (bool, str)
            (success, decrypted_value_or_error)
        """
        if not HAS_CRYPTO:
            return False, "pycryptodomex not installed"

        try:
            combined = bytes.fromhex(hex_value)
            salt = combined[:_SALT_LENGTH]
            nonce = combined[_SALT_LENGTH:_SALT_LENGTH + 16]
            tag = combined[_SALT_LENGTH + 16:_SALT_LENGTH + 32]
            ciphertext = combined[_SALT_LENGTH + 32:]

            key = PBKDF2(
                self._passphrase.encode('utf-8'),
                salt,
                dkLen=_KEY_LENGTH,
                count=_PBKDF2_ITERATIONS,
            )
            cipher = AES.new(key, AES.MODE_GCM, nonce=nonce)
            plaintext = cipher.decrypt_and_verify(ciphertext, tag)
            return True, plaintext.decode('utf-8')
        except Exception as e:
            return False, f"Value decryption failed: {e}"
