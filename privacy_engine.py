import re
import uuid

class PrivacyEngine:
    def __init__(self):
        self.value_to_token = {}
        self.token_to_value = {}

        self.sensitive_fields = {
            "mobileNo": "PHONE",
            "uidNo": "UID",
            "lat": "LAT",
            "lon": "LON",
            "latitude": "LAT",
            "longitude": "LON"
        }

    def get_or_create_token(self, prefix, value):
        val_str = str(value).strip()
        # Clean any trailing .0 for floats/integers
        if val_str.endswith(".0"):
            val_str = val_str[:-2]
            
        if not val_str:
            return val_str
            
        if val_str in self.value_to_token:
            return self.value_to_token[val_str]
            
        # Generate token in the format <//PREFIX-UUID//>
        # Using first 12 characters of UUID to keep it readable and clean but completely unique
        unique_id = str(uuid.uuid4().hex[:12])
        token = f"<//{prefix}-{unique_id}//>"
        
        self.value_to_token[val_str] = token
        self.token_to_value[token] = val_str
        return token

    def encrypt_record(self, record):
        """Encrypt sensitive fields in a dictionary record."""
        if not isinstance(record, dict):
            return record
            
        encrypted = {}
        for key, value in record.items():
            if key in self.sensitive_fields:
                prefix = self.sensitive_fields[key]
                encrypted[key] = self.get_or_create_token(prefix, value)
            else:
                if isinstance(value, dict):
                    encrypted[key] = self.encrypt_record(value)
                elif isinstance(value, list):
                    encrypted[key] = [self.encrypt_record(item) if isinstance(item, dict) else item for item in value]
                else:
                    encrypted[key] = value
        return encrypted

    def decrypt_value(self, val):
        """Decrypt a single token back to its value."""
        val_str = str(val).strip()
        return self.token_to_value.get(val_str, val)

    def decrypt_record(self, record):
        """Decrypt sensitive fields in a dictionary record."""
        if not isinstance(record, dict):
            return record
            
        decrypted = {}
        for key, value in record.items():
            value_str = str(value)
            if value_str in self.token_to_value:
                decrypted[key] = self.token_to_value[value_str]
            else:
                if isinstance(value, dict):
                    decrypted[key] = self.decrypt_record(value)
                elif isinstance(value, list):
                    decrypted[key] = [self.decrypt_record(item) if isinstance(item, dict) else item for item in value]
                else:
                    decrypted[key] = value
        return decrypted

    def tokenize_text(self, text):
        """Finds sensitive patterns (UID, Mobile numbers) in raw text and replaces them with tokens."""
        if not isinstance(text, str):
            return text
            
        # 1. Match mobile numbers (10 digits)
        def replace_phone(match):
            val = match.group(0)
            return self.get_or_create_token("PHONE", val)
            
        text = re.sub(r'\b\d{10}\b', replace_phone, text)

        # 2. Match UIDs (7 digits starting with 59, or 11 digits)
        def replace_uid(match):
            val = match.group(0)
            return self.get_or_create_token("UID", val)
            
        # Match 11-digit numbers first
        text = re.sub(r'\b\d{11}\b', replace_uid, text)
        # Match 7-digit UIDs starting with 59
        text = re.sub(r'\b59\d{5}\b', replace_uid, text)
        
        return text

    def detokenize_text(self, text):
        """Replaces all tokens of the form <//PREFIX-UUID//> back to their original values in text."""
        if not isinstance(text, str):
            return text
            
        token_pattern = re.compile(r'<//[A-Z]+-[a-f0-9]+//>')
        
        def replace_token(match):
            token = match.group(0)
            return self.token_to_value.get(token, token)
            
        return token_pattern.sub(replace_token, text)
