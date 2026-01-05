import os
import io
import time
import fitz  # PyMuPDF
from google.cloud import dlp_v2
from google.cloud import vision
from google.cloud import translate_v3 as translate
from typing import List

class ClinicalDocumentProcessor:
    def __init__(self, project_id: str, location: str = "global", credentials_file: str = None, log_callback=None):
        self.project_id = project_id
        self.location = location
        self.log_callback = log_callback
        
        if credentials_file:
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_file
        
        self.dlp_client = dlp_v2.DlpServiceClient()
        self.vision_client = vision.ImageAnnotatorClient()
        self.translate_client = translate.TranslationServiceClient()

    def log(self, message, metadata=None):
        if self.log_callback:
            if metadata:
                self.log_callback(f"{message} [METADATA:{metadata}]")
            else:
                self.log_callback(message)
        else:
            print(message)

    def process_document(self, filepath: str, custom_terms: List[str] = None) -> bytes:
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
        
        # Add Custom Terms if provided
        custom_info_types = []
        if custom_terms:
            self.log(f"Adding {len(custom_terms)} custom terms to redaction list...")
            custom_info_types.append({
                "info_type": {"name": "CUSTOM_REDACTION_LIST"},
                "likelihood": dlp_v2.Likelihood.VERY_LIKELY,
                "dictionary": {
                    "word_list": {"words": custom_terms}
                }
            })
            inspect_config["custom_info_types"] = custom_info_types

        # Detect PDF
        is_pdf = filepath.lower().endswith(".pdf")
        
        try:
            if is_pdf:
                return self._process_pdf(filepath, inspect_config)
            else:
                # Fallback for simple images
                img_bytes = self._process_image(filepath, inspect_config)
                return img_bytes

        except Exception as e:
            error_str = str(e)
            self.log(f"Failed to redact {filename}: {error_str}")
            raise e

    def _process_image(self, filepath: str, inspect_config) -> bytes:
        with open(filepath, "rb") as f:
            image_bytes = f.read()
        return self._redact_image_bytes(image_bytes, inspect_config)

    def _redact_image_bytes(self, image_bytes: bytes, inspect_config) -> bytes:
        """Native image redaction (returns modified pixels)"""
        parent = f"projects/{self.project_id}/locations/global"
        image_redactions = []
        for it in inspect_config.get("info_types", []):
            image_redactions.append({"info_type": it, "redaction_color": {"red": 0, "green": 0, "blue": 0}})
        if "custom_info_types" in inspect_config:
            for cit in inspect_config["custom_info_types"]:
                image_redactions.append({"info_type": cit["info_type"], "redaction_color": {"red": 0, "green": 0, "blue": 0}})

        byte_item = {"type_": dlp_v2.ByteContentItem.BytesType.IMAGE_PNG, "data": image_bytes}
        response = self.dlp_client.redact_image(
            request={
                "parent": parent,
                "inspect_config": inspect_config,
                "image_redactions": image_redactions,
                "byte_item": byte_item
            }
        )
        return response.redacted_image

    def _process_pdf(self, filepath: str, inspect_config) -> bytes:
        """
        1. Native Redaction on original PDF
        2. Flattening (Convert to Image) to permanently remove underlying text
        3. OCR Overlay for 100% selectability
        """
        doc = fitz.open(filepath)
        total_pages = len(doc)
        output_doc = fitz.open() # create new empty PDF
        
        self.log(f"Processing PDF (Anonymizing + Flattening + Searchable OCR Overlay)...", metadata={"pages": total_pages})
        
        parent = f"projects/{self.project_id}/locations/global"
        zoom = 3.0
        mat = fitz.Matrix(zoom, zoom)

        for i in range(total_pages):
            page = doc.load_page(i)
            self.log(f"Analyzing & Digitalizing Page {i+1}/{total_pages}...")
            
            try:
                # STAGE 1: NATIVE REDACTION
                # Render to find coordinates
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                
                # Inspect via DLP
                item = {"byte_item": {"type_": dlp_v2.ByteContentItem.BytesType.IMAGE_PNG, "data": img_bytes}}
                response = self.dlp_client.inspect_content(
                    request={"parent": parent, "inspect_config": inspect_config, "item": item}
                )
                
                findings = response.result.findings
                if findings:
                    self.log(f"       Found {len(findings)} sensitive items. Applying native redactions...")
                    for finding in findings:
                        for loc in finding.location.content_locations:
                            image_loc = getattr(loc, "image_location", None)
                            if image_loc and image_loc.bounding_boxes:
                                for box in image_loc.bounding_boxes:
                                    # Translate coordinates back to PDF points
                                    rect = fitz.Rect(box.left / zoom, box.top / zoom, 
                                                    (box.left + box.width) / zoom, (box.top + box.height) / zoom)
                                    page.add_redact_annot(rect, fill=(0, 0, 0))
                    page.apply_redactions()

                # STAGE 2: FLATTENING & BURNING
                # Render the *redacted* page (burns in all black boxes)
                pix_redacted = page.get_pixmap(matrix=mat)
                redacted_img_bytes = pix_redacted.tobytes("png")
                
                # Create a clean page in the output document
                new_page = output_doc.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(page.rect, stream=redacted_img_bytes)

                # STAGE 3: CLOUD OCR OVERLAY
                # Vision OCR on the flat image
                vision_image = vision.Image(content=redacted_img_bytes)
                vision_response = self.vision_client.document_text_detection(image=vision_image)
                
                if vision_response.full_text_annotation:
                    # Place a hidden text layer
                    for page_v in vision_response.full_text_annotation.pages:
                        for block in page_v.blocks:
                            for paragraph in block.paragraphs:
                                for word in paragraph.words:
                                    word_text = "".join([l.text for l in word.symbols])
                                    vertices = word.bounding_box.vertices
                                    x0 = min(v.x for v in vertices) / zoom
                                    y0 = min(v.y for v in vertices) / zoom
                                    x1 = max(v.x for v in vertices) / zoom
                                    y1 = max(v.y for v in vertices) / zoom
                                    
                                    # Insert hidden text
                                    new_page.insert_text((x0, y1), word_text, fontsize=(y1-y0)*0.8, render_mode=3)
                                    
            except Exception as e:
                self.log(f"       Error on page {i+1}: {e}")
                
            self.log(f"Page {i+1} completed", metadata={"page_done": i+1})

        # Save
        self.log("Compiling document...", metadata={"save_start": 0})
        
        output_doc.set_metadata({})
        
        out_stream = io.BytesIO()
        output_doc.save(out_stream, garbage=4, deflate=True)
        doc_bytes = out_stream.getvalue()
        
        doc.close()
        output_doc.close()
        
        self.log("Success! Redacted searchable PDF generated. (Flattened)", metadata={"save_done": True})
        return doc_bytes

    def translate_document(self, doc_bytes: bytes, target_language: str = "en") -> List[tuple]:
        """
        Translates a PDF document using Google Cloud Translation AI.
        Dynamically splits the document into chunks where each chunk is < 30MB 
        (to stay well within Google's 40MiB synchronous payload limit).
        """
        try:
            doc = fitz.open("pdf", doc_bytes)
            total_pages = len(doc)
            results = []
            
            MAX_PAYLOAD_BYTES = 30 * 1024 * 1024  # 30MB extra-safe limit (API limit is 40MiB)
            
            self.log(f"Analyzing {total_pages} pages for dynamic chunking...")
            
            current_chunk_doc = fitz.open()
            current_start_idx = 0
            chunk_num = 1
            
            for i in range(total_pages):
                self.log(f"Preparing Page {i+1}...", metadata={"trans_flatten_start": True})
                # We flatten page-by-page to check size
                page = doc.load_page(i)
                zoom = 2.0  # High quality
                mat = fitz.Matrix(zoom, zoom)
                pix = page.get_pixmap(matrix=mat)
                img_bytes = pix.tobytes("png")
                
                # Try adding to current chunk
                temp_page = current_chunk_doc.new_page(width=page.rect.width, height=page.rect.height)
                temp_page.insert_image(page.rect, stream=img_bytes)
                self.log(f"Page {i+1} flattened.", metadata={"trans_flatten_done": True})
                
                # Check resulting size
                current_size = len(current_chunk_doc.tobytes())
                
                if current_size > MAX_PAYLOAD_BYTES and i > current_start_idx:
                    # Current page pushed us over the limit
                    current_chunk_doc.delete_page(len(current_chunk_doc) - 1)
                    
                    # Finalize previous chunk
                    chunk_label = f"{current_start_idx+1:02d}-{i:02d}"
                    chunk_bytes = current_chunk_doc.tobytes()
                    self.log(f"Sending Chunk {chunk_num} (Pages {chunk_label}, {round(len(chunk_bytes)/(1024*1024), 1)}MB) to API...")
                    
                    self.log(f"Translating...", metadata={"trans_api_start": len(chunk_bytes)})
                    translated_bytes = self._call_translate_api(chunk_bytes, target_language)
                    self.log(f"Chunk {chunk_num} completed.", metadata={"trans_api_done": True})
                    
                    results.append((chunk_label, translated_bytes))
                    
                    # Start new chunk with the current page
                    current_chunk_doc.close()
                    current_chunk_doc = fitz.open()
                    current_start_idx = i
                    chunk_num += 1
                    
                    self.log(f"Retrying Page {i+1} in new chunk...", metadata={"trans_flatten_start": True})
                    new_temp_page = current_chunk_doc.new_page(width=page.rect.width, height=page.rect.height)
                    new_temp_page.insert_image(page.rect, stream=img_bytes)
                    self.log(f"Page {i+1} moved to new chunk.", metadata={"trans_flatten_done": True})
            
            # Send the final chunk
            if len(current_chunk_doc) > 0:
                chunk_label = f"{current_start_idx+1:02d}-{total_pages:02d}"
                chunk_bytes = current_chunk_doc.tobytes()
                # If it's the only chunk, we don't need the label
                actual_label = "" if chunk_num == 1 else chunk_label
                
                self.log(f"Sending Final Chunk (Pages {chunk_label}, {round(len(chunk_bytes)/(1024*1024), 1)}MB) to API...")
                self.log(f"Translating...", metadata={"trans_api_start": len(chunk_bytes)})
                translated_bytes = self._call_translate_api(chunk_bytes, target_language)
                self.log(f"Final chunk completed.", metadata={"trans_api_done": True})
                
                results.append((actual_label, translated_bytes))

            current_chunk_doc.close()
            doc.close()
            return results

        except Exception as e:
            self.log(f"Dynamic translation failed: {e}")
            raise e

    def _call_translate_api(self, doc_bytes: bytes, target_language: str) -> bytes:
        """Internal helper to call the Google Translation API for a single PDF byte stream."""
        # Translation API Advanced requires a specific location for document translation.
        # Document translation is currently only supported in 'us-central1' or 'global'.
        location = "us-central1"
        parent = f"projects/{self.project_id}/locations/{location}"

        document_input_config = {
            "content": doc_bytes,
            "mime_type": "application/pdf",
        }

        response = self.translate_client.translate_document(
            request={
                "parent": parent,
                "target_language_code": target_language,
                "document_input_config": document_input_config,
            }
        )

        doc_trans = response.document_translation
        if hasattr(doc_trans, "byte_content") and doc_trans.byte_content:
            return doc_trans.byte_content
        elif hasattr(doc_trans, "content") and doc_trans.content:
            return doc_trans.content
        elif hasattr(doc_trans, "byte_stream_outputs") and doc_trans.byte_stream_outputs:
            return b"".join(doc_trans.byte_stream_outputs)
        else:
            raise AttributeError("Could not extract bytes from DocumentTranslation response.")

    def _flatten_pdf(self, doc_bytes: bytes) -> bytes:
        """
        Converts each page of the PDF into a high-res image and re-assembles it.
        This "Option 1" forces Google Translate to use its image-painting logic,
        which is much better at removing the original background text.
        """
        doc = fitz.open("pdf", doc_bytes)
        new_doc = fitz.open()
        
        zoom = 2.0  # High quality
        mat = fitz.Matrix(zoom, zoom)
        
        for i in range(len(doc)):
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=mat)
            img_bytes = pix.tobytes("png")
            
            # Create a new page with the same dimensions
            new_page = new_doc.new_page(width=page.rect.width, height=page.rect.height)
            # Insert the image to cover the whole page
            new_page.insert_image(page.rect, stream=img_bytes)
            
        # Ensure no metadata is carried over to the translation
        new_doc.set_metadata({})
        
        flattened_bytes = new_doc.tobytes()
        doc.close()
        new_doc.close()
        return flattened_bytes
