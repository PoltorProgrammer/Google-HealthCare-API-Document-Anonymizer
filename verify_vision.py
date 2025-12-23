import os
from google.cloud import vision
import google.auth

# Force credential usage
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credentials.json'

def verify_vision():
    try:
        print("1. Loading Credentials...")
        creds, project = google.auth.default()
        print(f"   Authenticated: {project}")

        client = vision.ImageAnnotatorClient()
        
        print("\n2. Testing Vision API (OCR)...")
        # Create a simple image (1x1 pixel black png)
        # This is just to test Authentication/Permission, not OCR quality.
        image_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
        
        image = vision.Image(content=image_content)
        
        response = client.document_text_detection(image=image)
        
        if response.error.message:
            raise Exception(f"API Error: {response.error.message}")
            
        print("   [+] Success! Vision API is accessible.")

    except Exception as e:
        print(f"\n[ERROR] Vision Test Failed: {e}")
        if "403" in str(e):
             print("    (!) PERMISSION DENIED. You need a role like 'Cloud Vision API User' or 'Editor'.")

if __name__ == "__main__":
    verify_vision()
