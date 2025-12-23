import os
from google.cloud import storage
import google.auth

# Force credential usage
os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = 'credentials.json'

def verify_storage():
    try:
        print("1. Loading Credentials...")
        creds, project = google.auth.default()
        print(f"   Authenticated: {project}")

        bucket_name = f"dlp-transient-test-{project}"
        print(f"\n2. Testing Storage Access (Bucket: {bucket_name})...")
        
        storage_client = storage.Client()
        
        # Try to create a bucket
        try:
            bucket = storage_client.create_bucket(bucket_name, location="europe-west6")
            print("   [+] Bucket created successfully.")
        except Exception as e:
            if "409" in str(e):
                 print("   [~] Bucket already exists.")
                 bucket = storage_client.get_bucket(bucket_name)
            else:
                 raise e
        
        # Upload file
        blob = bucket.blob("test_file.txt")
        blob.upload_from_string("Test Content")
        print("   [+] File uploaded successfully.")
        
        # Clean up
        blob.delete()
        print("   [+] File deleted.")
        # bucket.delete() # Keep bucket for future use? No, delete for test.
        # print("   [+] Bucket deleted.")
        
        print("\n[SUCCESS] Storage Access Verified.")

    except Exception as e:
        print(f"\n[ERROR] Storage Test Failed: {e}")
        if "403" in str(e):
             print("    (!) PERMISSION DENIED: Service Account needs 'Storage Admin' or 'Storage Object Admin'.")

if __name__ == "__main__":
    verify_storage()
