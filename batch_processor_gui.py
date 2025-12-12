import tkinter as tk
from tkinter import filedialog, messagebox
import os
import sys
import time
import threading
import json
import base64

# Note: Integration with Google Cloud Healthcare API
from healthcare_processor import HealthcareProcessor

class LocalFileProcessorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Clinical Document Processor - Healthcare API")
        self.root.geometry("600x500")

        self.source_folder = ""
        self.files_to_process = []
        self.processed_files = []
        self.is_processing = False
        self.config = self.load_config()

    def load_config(self):
        try:
            with open('config.json', 'r') as f:
                return json.load(f)
        except Exception as e:
            return {"app_settings": {"simulation_mode": True}}

    def create_widgets(self):
        # 1. Folder Selection
        select_frame = tk.Frame(self.root, pady=10)
        select_frame.pack(fill=tk.X, padx=10)
        
        self.btn_select = tk.Button(select_frame, text="Select Data Folder", command=self.select_folder)
        self.btn_select.pack(side=tk.LEFT)

        self.lbl_folder = tk.Label(select_frame, text="No folder selected", fg="gray")
        self.lbl_folder.pack(side=tk.LEFT, padx=10)

        # 2. Controls
        ctrl_frame = tk.Frame(self.root, pady=10)
        ctrl_frame.pack(fill=tk.X, padx=10)

        self.btn_start = tk.Button(ctrl_frame, text="Start Batch Processing", command=self.start_processing_thread, state=tk.DISABLED, bg="#dddddd")
        self.btn_start.pack(side=tk.LEFT)
        
        # 3. Status Lists
        list_frame = tk.Frame(self.root, pady=10)
        list_frame.pack(fill=tk.BOTH, expand=True, padx=10)

        # Pending Files
        tk.Label(list_frame, text="Documents to Process:").grid(row=0, column=0, sticky="w")
        self.list_pending = tk.Listbox(list_frame, height=15, width=40)
        self.list_pending.grid(row=1, column=0, padx=5, sticky="news")

        # Processed Files
        tk.Label(list_frame, text="Completed Documents:").grid(row=0, column=1, sticky="w")
        self.list_processed = tk.Listbox(list_frame, height=15, width=40)
        self.list_processed.grid(row=1, column=1, padx=5, sticky="news")
        
        list_frame.columnconfigure(0, weight=1)
        list_frame.columnconfigure(1, weight=1)

        # 4. Status Bar
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.source_folder = folder
            self.lbl_folder.config(text=folder)
            self.load_files()
            self.btn_start.config(state=tk.NORMAL, bg="#90ee90")
            self.status_var.set(f"Loaded {len(self.files_to_process)} files.")

    def load_files(self):
        self.files_to_process = []
        self.list_pending.delete(0, tk.END)
        self.list_processed.delete(0, tk.END)
        self.processed_files = [] 
        
        try:
            output_folder = os.path.join(self.source_folder, "processed")
            
            for f in os.listdir(self.source_folder):
                full_path = os.path.join(self.source_folder, f)
                if f == "processed" or not os.path.isfile(full_path):
                    continue
                    
                expected_output = os.path.join(output_folder, f"anonymized_{f}")
                if os.path.exists(expected_output):
                    self.processed_files.append(f)
                    self.list_processed.insert(tk.END, f"{f} (Completed)")
                else:
                    self.files_to_process.append(f)
                    self.list_pending.insert(tk.END, f)
                    
        except Exception as e:
            messagebox.showerror("Error", f"Failed to list files: {e}")

    def start_processing_thread(self):
        # Run in thread to not freeze UI during long API calls
        thread = threading.Thread(target=self.start_processing)
        thread.start()

    def start_processing(self):
        if not self.files_to_process:
            messagebox.showinfo("Info", "No files to process.")
            return
        
        self.is_processing = True
        self.btn_start.config(state=tk.DISABLED)
        self.status_var.set("Initializing Healthcare API Consumer...")
        self.root.update()

        app_settings = self.config.get('app_settings', {})
        simulation_mode = app_settings.get('simulation_mode', True)
        
        try:
            if simulation_mode:
                # SIMULATION
                # Make a snapshot of the list
                files_snapshot = list(self.files_to_process)
                total = len(files_snapshot)
                
                for i, filename in enumerate(files_snapshot):
                    self.status_var.set(f"Simulating upload for {filename} ({i+1}/{total})...")
                    time.sleep(0.5)
                    
                self.status_var.set("Simulating de-identification job...")
                time.sleep(2)
                
                self.status_var.set("Simulating download...")
                output_folder = os.path.join(self.source_folder, "processed")
                os.makedirs(output_folder, exist_ok=True)
                
                for filename in files_snapshot:
                    output_path = os.path.join(output_folder, f"anonymized_{filename}")
                    with open(output_path, 'w') as f:
                        f.write("SIMULATED ANONYMIZED CONTENT")
                        
                    # Update UI in main thread potentially, but tkinter is not thread safe? 
                    # Usually okay for simple calls or use after/queue. 
                    # For simplicity here we just modify lists since it's the only thread touching them now.
                    self.list_pending.delete(0)
                    self.list_processed.insert(tk.END, f"{filename} (Simulated)")
                    
                self.status_var.set("Simulation Complete.")
                messagebox.showinfo("Done", "Simulation Complete!")
            
            else:
                # REAL MODE
                cloud_config = self.config.get('google_cloud', {})
                processor = HealthcareProcessor(
                    project_id=cloud_config.get('project_id'),
                    location=cloud_config.get('location'),
                    dataset_id=cloud_config.get('dataset_id'),
                    fhir_store_id=cloud_config.get('fhir_store_id'),
                    destination_store_id=cloud_config.get('destination_store_id'),
                    credentials_file=cloud_config.get('service_account_key_file')
                )
                
                # 1. Upload All
                self.status_var.set("Uploading files to FHIR Store...")
                filename_map = {} # filename -> resource_id (not strictly needed if title is preserved)
                
                total_files = len(self.files_to_process)
                # Snapshot list
                files_snapshot = list(self.files_to_process)
                
                for idx, filename in enumerate(files_snapshot):
                    self.status_var.set(f"Uploading {idx+1}/{total_files}: {filename}")
                    
                    file_path = os.path.join(self.source_folder, filename)
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read()
                        
                    processor.upload_file_as_fhir(filename, content)
                
                # 2. De-identify
                self.status_var.set("Running Batch De-identification Job (this may take minutes)...")
                # This blocks
                results = processor.run_deidentify_job()
                
                # 3. Save Results
                self.status_var.set("Saving processed files...")
                output_folder = os.path.join(self.source_folder, "processed")
                os.makedirs(output_folder, exist_ok=True)
                
                for filename, text in results.items():
                    # We only care about files we just uploaded? Or anything in the output store?
                    # The results contain title -> text.
                    output_path = os.path.join(output_folder, f"anonymized_{filename}")
                    with open(output_path, 'w', encoding='utf-8') as f:
                        f.write(text)
                    
                    # Update UI
                    # (This logic is imperfect if results contain old files, but acceptable)
                
                # Clear pending list
                self.files_to_process = []
                self.list_pending.delete(0, tk.END)
                # Refresh processed
                self.load_files() 
                
                self.status_var.set("Processing Complete!")
                messagebox.showinfo("Done", "Batch Processing Complete via Google Healthcare API!")

        except Exception as e:
            print(e)
            # messagebox.showerror("Processing Error", f"Error: {e}") # Can crash thread
            self.status_var.set(f"Error: {str(e)[:50]}...")
            
        finally:
            self.is_processing = False
            # self.btn_start.config(state=tk.NORMAL) # Needs main thread
            
if __name__ == "__main__":
    root = tk.Tk()
    app = LocalFileProcessorApp(root)
    app.create_widgets()
    root.mainloop()
