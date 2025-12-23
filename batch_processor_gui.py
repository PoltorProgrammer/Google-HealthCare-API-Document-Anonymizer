import tkinter as tk
from tkinter import filedialog, messagebox
import os
import sys
import time
import threading
import json
import base64
import mimetypes

# Note: Integration with Google Cloud DLP (Data Loss Prevention)
from dlp_processor import ClinicalDocumentProcessor

class LocalFileProcessorApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Clinical Document Processor - Google DLP")
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

        # 4. Log Box
        log_frame = tk.Frame(self.root, pady=5)
        log_frame.pack(fill=tk.BOTH, expand=True, padx=10)
        
        tk.Label(log_frame, text="Execution Log:").pack(anchor="w")
        self.text_log = tk.Text(log_frame, height=8, state=tk.DISABLED)
        self.text_log.pack(fill=tk.BOTH, expand=True)

        # 5. Status Bar
        self.status_var = tk.StringVar()
        self.status_var.set("Ready")
        self.status_bar = tk.Label(self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor=tk.W)
        self.status_bar.pack(side=tk.BOTTOM, fill=tk.X)

    def log_message(self, message):
        self.status_var.set(message)
        self.text_log.config(state=tk.NORMAL)
        self.text_log.insert(tk.END, f"{time.strftime('%H:%M:%S')} - {message}\n")
        self.text_log.see(tk.END)
        self.text_log.config(state=tk.DISABLED)
        self.root.update()

    def select_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.source_folder = folder
            self.lbl_folder.config(text=folder)
            self.load_files()
            self.btn_start.config(state=tk.NORMAL, bg="#90ee90")
            self.log_message(f"Loaded {len(self.files_to_process)} files from {folder}")

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
                
                # Filter out system/binary files that are definitely not clinical docs
                if f.startswith('.') or f.lower().endswith(('.ds_store', '.exe', '.py', '.pyc', '.json', '.bat', '.sh', '.command', '.dll', '.bin')):
                    continue
                    
                expected_output = os.path.join(output_folder, f"anonymized_{f}")
                if os.path.exists(expected_output):
                    self.processed_files.append(f)
                    self.list_processed.insert(tk.END, f"{f} (Completed)")
                else:
                    self.files_to_process.append(f)
                    self.list_pending.insert(tk.END, f)
                    
        except Exception as e:
            self.log_message(f"Error listing files: {e}")
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
        self.log_message("Initializing DLP Processor...")
        
        app_settings = self.config.get('app_settings', {})
        simulation_mode = app_settings.get('simulation_mode', True)
        
        try:
            if simulation_mode:
                # SIMULATION
                # Make a snapshot of the list
                files_snapshot = list(self.files_to_process)
                total = len(files_snapshot)
                
                for i, filename in enumerate(files_snapshot):
                    self.log_message(f"Simulating upload for {filename} ({i+1}/{total})...")
                    time.sleep(0.5)
                    
                self.log_message("Simulating de-identification job...")
                time.sleep(2)
                
                self.log_message("Simulating download...")
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
                    
                self.log_message("Simulation Complete.")
                messagebox.showinfo("Done", "Simulation Complete!")
            
            else:
                # REAL MODE - Direct DLP (Transient)
                cloud_config = self.config.get('google_cloud', {})
                processor = ClinicalDocumentProcessor(
                    project_id=cloud_config.get('project_id'),
                    location=cloud_config.get('location'),
                    credentials_file=cloud_config.get('service_account_key_file')
                )
                
                # Setup output folder
                output_folder = os.path.join(self.source_folder, "processed")
                os.makedirs(output_folder, exist_ok=True)

                total_files = len(self.files_to_process)
                files_snapshot = list(self.files_to_process)
                
                success_count = 0
                
                for idx, filename in enumerate(files_snapshot):
                    self.log_message(f"Processing {idx+1}/{total_files}: {filename}")
                    
                    file_path = os.path.join(self.source_folder, filename)
                    output_path = os.path.join(output_folder, f"anonymized_{filename}")
                    
                    try:
                        # Direct RAM-only processing
                        redacted_bytes = processor.process_document(file_path)
                        
                        if redacted_bytes:
                            with open(output_path, 'wb') as f:
                                f.write(redacted_bytes)
                            
                            success_count += 1
                        else:
                             self.log_message(f"Completed {filename} but no content returned?")

                    except Exception as e:
                        print(f"Error processing {filename}: {e}")
                        self.log_message(f"Failed {filename}: {str(e)[:50]}...")
                        # If failed, continue to next file
                        continue

                # Refresh processed
                self.files_to_process = []
                self.list_pending.delete(0, tk.END)
                self.load_files() 
                
                self.log_message(f"Complete! Processed {success_count}/{total_files} files.")
                messagebox.showinfo("Done", f"Batch Processing Complete via Google Cloud DLP!\n\nSuccessful: {success_count}\nFailed: {total_files - success_count}\n\nProcessed files saved to /processed folder.")

        except Exception as e:
            full_error = str(e)
            print(f"FULL ERROR TRACEBACK: {full_error}") 
            self.log_message(f"Error: {full_error}")
            
        finally:
            self.is_processing = False
            # self.btn_start.config(state=tk.NORMAL) # Needs main thread
            
if __name__ == "__main__":
    root = tk.Tk()
    app = LocalFileProcessorApp(root)
    app.create_widgets()
    root.mainloop()
