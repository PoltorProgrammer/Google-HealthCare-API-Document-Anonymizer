import os
import time
import base64
import json
from typing import List, Dict
import google.auth
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

class HealthcareProcessor:
    def __init__(self, project_id: str, location: str, dataset_id: str, 
                 fhir_store_id: str, destination_store_id: str, credentials_file: str = None):
        """
        Initialize the Google Cloud Healthcare API client (Discovery).
        """
        self.project_id = project_id
        self.location = location
        self.dataset_id = dataset_id
        self.fhir_store_id = fhir_store_id
        self.destination_store_id = destination_store_id

        # Authenticate
        if credentials_file and os.path.exists(credentials_file):
            os.environ['GOOGLE_APPLICATION_CREDENTIALS'] = credentials_file
        
        creds, project = google.auth.default()
        self.service = build('healthcare', 'v1', credentials=creds)

        self.parent = f"projects/{project_id}/locations/{location}/datasets/{dataset_id}"
        self.fhir_store_path = f"{self.parent}/fhirStores/{fhir_store_id}"

    def upload_file_as_fhir(self, filename: str, content: str) -> str:
        """
        Wraps text content in a FHIR DocumentReference and uploads it.
        Returns the Resource ID.
        """
        encoded = base64.b64encode(content.encode('utf-8')).decode('utf-8')
        
        doc_ref = {
            "resourceType": "DocumentReference",
            "status": "current",
            "docStatus": "final",
            "type": {
                "text": "Clinical Note"
            },
            "content": [
                {
                    "attachment": {
                        "contentType": "text/plain",
                        "data": encoded,
                        "title": filename 
                    }
                }
            ]
        }
        
        # projects.locations.datasets.fhirStores.fhir.create
        request = self.service.projects().locations().datasets().fhirStores().fhir().create(
            parent=self.fhir_store_path,
            type="DocumentReference",
            body=doc_ref
        )
        
        response = request.execute()
        # Response is the created resource (JSON) containing "id"
        return response.get("id")

    def run_deidentify_job(self) -> Dict[str, str]:
        """
        Triggers a de-identify operation from source store to dest store.
        Waits for completion.
        Returns a mapping of Original Filenames -> De-identified Text.
        """
        # De-identify Configuration
        deid_config = {
            "config": {
                "fhir": {
                    "textConfig": {
                        "transformations": [
                            {"infoTypes": ["PERSON_NAME", "DATE", "PHONE_NUMBER", "EMAIL_ADDRESS"]}
                        ]
                    }
                }
            },
            "destinationStore": f"{self.parent}/fhirStores/{self.destination_store_id}_{int(time.time())}"
        }

        # projects.locations.datasets.fhirStores.deidentify
        operation = self.service.projects().locations().datasets().fhirStores().deidentify(
            sourceStore=self.fhir_store_path,
            body=deid_config
        ).execute()
        
        print(f"De-id operation started: {operation.get('name')}")
        
        # Wait for operation
        # Operation name: projects/.../locations/.../datasets/.../operations/...
        op_name = operation.get('name')
        
        while True:
            # projects.locations.datasets.operations.get
            op_status = self.service.projects().locations().datasets().operations().get(
                name=op_name
            ).execute()
            
            if op_status.get('done'):
                if 'error' in op_status:
                    raise Exception(f"De-identify failed: {op_status['error']}")
                break
            
            time.sleep(5)
            
        print("De-id complete.")
        
        run_dest_store_id = deid_config['destinationStore'].split('/')[-1]
        return self.fetch_processed_results(run_dest_store_id)

    def fetch_processed_results(self, store_id: str) -> Dict[str, str]:
        results = {}
        store_path = f"{self.parent}/fhirStores/{store_id}"
        
        # projects.locations.datasets.fhirStores.fhir.search
        # Search for DocumentReference
        request = self.service.projects().locations().datasets().fhirStores().fhir().search(
            parent=store_path,
            resourceType="DocumentReference"
        )
        
        response = request.execute()
        resources = response.get('entry', [])
        
        for entry in resources:
            resource = entry.get('resource', {})
            try:
                content_list = resource.get("content", [])
                for content in content_list:
                    pk = content.get("attachment", {})
                    title = pk.get("title", "unknown.txt")
                    b64_data = pk.get("data", "")
                    
                    if b64_data:
                        text = base64.b64decode(b64_data).decode('utf-8')
                        results[title] = text
            except Exception as e:
                print(f"Error parsing resource: {e}")
                 
        return results
