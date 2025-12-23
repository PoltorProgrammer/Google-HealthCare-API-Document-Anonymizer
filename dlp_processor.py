import os
import time
import io
from typing import List, Dict, Tuple
import google.auth
from google.cloud import dlp_v2
import fitz  # PyMuPDF

class ClinicalDocumentProcessor:
    def __init__(self, project_id: str, location: str, credentials_file: str = None):
        """
        Initialize the Google Cloud DLP client.
        Uses in-memory processing. PDFs are rasterized to images, redacted, and re-assembled.
        """
        self.project_id = project_id
        self.location = "global" 

        # Authenticate
        if credentials_file and os.path.exists(credentials_file):
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_file
        
        creds, project = google.auth.default()
        self.dlp_client = dlp_v2.DlpServiceClient(credentials=creds)
        
        print(f"   [Init] DLP Processor ready.")

    def process_document(self, filepath: str) -> bytes:
        filename = os.path.basename(filepath)
        
        # InfoTypes Config
        info_types = [
            {"name": "PERSON_NAME"}, 
            {"name": "PHONE_NUMBER"}, 
            {"name": "EMAIL_ADDRESS"}, 
            {"name": "CREDIT_CARD_NUMBER"},
            {"name": "STREET_ADDRESS"},
            {"name": "PASSPORT"},
            
            # Germany
            {"name": "GERMANY_PASSPORT"},
            {"name": "GERMANY_IDENTITY_CARD_NUMBER"},
            {"name": "GERMANY_DRIVERS_LICENSE_NUMBER"},
            {"name": "GERMANY_TAXPAYER_IDENTIFICATION_NUMBER"},
            {"name": "GERMANY_SCHUFA_ID"},
            
            # Switzerland
            {"name": "SWITZERLAND_SOCIAL_SECURITY_NUMBER"},
            
            # Austria
            {"name": "AUSTRIA_SOCIAL_SECURITY_NUMBER"}, 

            # General / Other
            {"name": "IBAN_CODE"},
            {"name": "SWIFT_CODE"},
            {"name": "IMEI_HARDWARE_ID"},
            {"name": "IP_ADDRESS"}
        ]
        
        inspect_config = {
            "info_types": info_types,
            "min_likelihood": dlp_v2.Likelihood.POSSIBLE
        }
        
        # Redaction Config (Black Box)
        redact_configs = []
        for it in info_types:
            redact_configs.append({"info_type": it, "redaction_color": {"red": 0, "green": 0, "blue": 0}})

        # Detect PDF vs Image
        is_pdf = False
        try:
            with open(filepath, 'rb') as f:
                header = f.read(4)
                if header.startswith(b'%PDF'):
                    is_pdf = True
        except: pass
        if filepath.lower().endswith(".pdf"):
            is_pdf = True

        try:
            if is_pdf:
                # PDF: Redact -> .pdf
                pdf_bytes = self._process_pdf(filepath, inspect_config, redact_configs)
                return pdf_bytes
                
            else:
                img_bytes = self._process_image(filepath, inspect_config, redact_configs)
                return img_bytes

        except Exception as e:
            error_str = str(e)
            print(f"   [DLP] Failed to redact {filename}: {error_str}")
            if "403" in error_str:
                 print("\n   [!] PERMISSION ERROR: Access Denied.")
                 print("       Please check your Service Account permissions (DLP User) and Billing Status.\n")
            raise e

    def _process_image(self, filepath: str, inspect_config, redact_configs) -> bytes:
        print(f"   [DLP] Processing Image ({os.path.basename(filepath)})...")
        
        with open(filepath, "rb") as f:
            content = f.read()
            
        byte_type = dlp_v2.ByteContentItem.BytesType.BYTES_TYPE_UNSPECIFIED
        header = content[:4]
        if header.startswith(b'\x89PNG'): byte_type = dlp_v2.ByteContentItem.BytesType.IMAGE_PNG
        elif header.startswith(b'\xFF\xD8\xFF'): byte_type = dlp_v2.ByteContentItem.BytesType.IMAGE_JPEG
        
        item = {"byte_item": {"type_": byte_type, "data": content}}

        response = self.dlp_client.redact_image(
            request={
                "parent": f"projects/{self.project_id}/locations/{self.location}",
                "inspect_config": inspect_config,
                "image_redaction_configs": redact_configs,
                "byte_item": item["byte_item"]
            }
        )
        print(f"   [DLP] Success! Redacted Image.")
        
        return response.redacted_image

    def _process_pdf(self, filepath: str, inspect_config, redact_configs) -> bytes:
        print(f"   [DLP] Processing PDF by converting pages to images...")
        
        doc = fitz.open(filepath)
        output_doc = fitz.open() 
        
        parent = f"projects/{self.project_id}/locations/{self.location}"
        
        for i in range(len(doc)):
            page = doc.load_page(i)
            # Capture dimensions
            pdf_w, pdf_h = page.rect.width, page.rect.height
            
            pix = page.get_pixmap(matrix=fitz.Matrix(2, 2)) 
            img_bytes = pix.tobytes("png")
            
            print(f"     > Redacting Page {i+1}/{len(doc)}...")
            
            item = {"byte_item": {"type_": dlp_v2.ByteContentItem.BytesType.IMAGE_PNG, "data": img_bytes}}
            
            redacted_bytes = img_bytes
            
            try:
                response = self.dlp_client.redact_image(
                    request={
                        "parent": parent,
                        "inspect_config": inspect_config,
                        "image_redaction_configs": redact_configs,
                        "byte_item": item["byte_item"]
                    }
                )
                redacted_bytes = response.redacted_image
                
            except Exception as e:
                print(f"       [!] Failed to redact page {i+1}: {e}")
                
            img_stream = io.BytesIO(redacted_bytes)
            new_page = output_doc.new_page(width=pdf_w, height=pdf_h)
            new_page.insert_image(new_page.rect, stream=img_stream)
            
        out_stream = io.BytesIO()
        output_doc.save(out_stream)
        output_doc.close()
        doc.close()
        
        print(f"   [DLP] Success! PDF Re-assembled.")
        return out_stream.getvalue()



