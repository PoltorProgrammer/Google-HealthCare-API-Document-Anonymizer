# MediXtract DLP Integration Architecture

This document defines the technical architecture for integrating Google Cloud Data Loss Prevention (DLP) into the MediXtract workflow. It details the security model, processing pipeline, translation capabilities, and infrastructure constraints required for compliance and sustainability.

## 1. Security Architecture: Defense-in-Depth

MediXtract implements a "Defense-in-Depth" security model for processing medical records. While source documents undergo manual anonymization prior to ingestion, human error remains a vulnerability.

### 1.1 Automated Fail-Safe Layer
Google DLP functions as a secondary, automated control layer positioned between data ingestion and the AI extraction engine. Its primary role is to detect and neutralize residual Personally Identifiable Information (PII) missed during the manual redaction process.

## 2. Data Processing Pipeline

The data flow ensures privacy preservation, standardization, and integrity before any clinical extraction occurs.

1.  **Ingestion:** System receives "Pre-anonymized" PDF/Image Reports containing manual redactions.
2.  **DLP Inspection:** 
    - The artifact is submitted to the Google DLP API.
    - **Scope:** Scanning of non-redacted regions and Optical Character Recognition (OCR) layers.
    - **Target InfoTypes:** `PERSON_NAME`, `DATE_OF_BIRTH`, `SWISS_SOCIAL_SECURITY_NUMBER` (AHV), `MEDICAL_RECORD_ID`.
3.  **Sanitization & Flattening:**
    - **Redaction:** Automatic de-identification of detected ranges.
    - **Security Flattening:** The document undergoes a strict rasterization process. This "burns in" all redactions (both manual black bars and DLP overlays) into the pixel layer. This step guarantees that no "invisible" text or metadata persists beneath cosmetic black bars, addressing the vulnerability of potentially incomplete manual redactions.
4.  **Standardization (Translation):**
    - **Language Normalization:** The pipeline integrates cloud-native translation services capable of converting input documents into potentially any target language. For this specific implementation, this capability is utilized to **standardize all clinical records into English**, creating a unified linguistic baseline.
    - **Structural Preservation:** The translation engine preserves the original document topology. Tables, headers, key-value pairs, and labels retain their spatial formatting and layout, ensuring that clinical context and visual hierarchy are maintained during the language shift.
5.  **Reconstruction (Searchable Output):**
    - Despite the security flattening, the final output is regenerated as a high-fidelity, **selectable and filterable PDF**.
    - A fresh OCR layer is applied to the flattened, translated image, restoring text selectability for human analysts without compromising the permanency of the redactions.
6.  **Clinical Extraction:** The sanitized, double-anonymized, and English-standardized documents serve as the structured payload processed by MediXtract for clinical value abstraction.

## 3. Configuration & Handling of Pre-Redacted Data

Given the input variance (mixed PDF and image formats with existing redactions), specific configurations are applied to handle "black bar" artifacts and underlying text layers.

### 3.1 Dual-Layer Inspection (Visual & Hidden)
To address the risk of incomplete manual anonymization, the architecture inspects both the rendered image and the underlying data structure:
*   **Visual Layer (OCR):** OCR is enabled to scan the *rendered* pixels of the document. This captures text that is visible to a human reader but sits between existing physical black bars.
*   **Invisible Layer (Content Stream & Metadata):** The inspection simultaneously targets the raw text layer and bytecode. This serves two critical security functions:
    1.  **Sanitization:** It permanently erases document metadata (e.g., existing titles, author tags, edit history) that are not visible in the file content but persist in the header structures.
    2.  **Cosmetic Redaction Detection:** It identifies instances where PII is only visually masked by a black rectangle but remains searchable in the underlying text stream.

### 3.2 Sensitivity Tuning & Confidence Scoring
The architecture utilizes a two-tier confidence system to balance noise reduction with critical detection.

**Available Confidence Levels (Likelihood):** 
1. `VERY_UNLIKELY` (Highest Sensitivity / Most Noise)
2. `UNLIKELY`
3. **`POSSIBLE`** (Selected Baseline)
4. `LIKELY`
5. `VERY_LIKELY` (Lowest Sensitivity / Least Noise)

*   **Global Threshold (`min_likelihood`):** Set to **POSSIBLE**. This acts as the baseline filter, discarding "Unlikely" or "Very Unlikely" findings to prevent excessive statistical noise, while retaining any finding with at least partial evidence.
*   **Custom Term Boosting:** Any terms matched against the custom redaction dictionary are explicitly promoted to **VERY_LIKELY**, determining that known sensitive terms are *always* redacted regardless of context.
*   **Strategy:** The system is tuned to prioritize Recall over Precision. False Positives (redacting non-PII) are acceptable to guarantee zero-trust compliance. Furthermore, false positive redactions typically do not impede clinical extraction, as the AI models can infer the missing information from the surviving context.
    *   *Example:* A medication like "Amoxicillin D.T." might have the "D.T." (Dispersible Tablet) suffix mistakenly redacted as initials. However, the extraction AI can deductively recover this information. By analyzing the visible "Amoxicillin" string alongside the dosage and administration context (e.g., "dissolve before use"), the model infers the specific formulation, effectively neutralizing the over-redaction.

## 4. Data Residency & Sovereignty

Adhering to strict international privacy standards is paramount. While the system processes data originating from **Switzerland**, **Germany (EU)**, and potentially other jurisdictions, it adopts a "Strictest-First" policy.

By anchoring infrastructure in **Switzerland**, the architecture satisfies the Swiss Federal Data Protection Act (**nFADP/DSG**) while simultaneously remaining fully compliant with the European **GDPR** (due to Switzerland's "Adequacy Decision" status granted by the European Commission). This centralization ensures a unified high-security enclosure for all inflows.
*   *Reference:* [EU Commission Adequacy Decision for Switzerland](https://commission.europa.eu/law/law-topic/data-protection/international-dimension-data-protection/adequacy-decisions_en)
*   *Reference:* [Federal Act on Data Protection (nFADP)](https://www.fedlex.admin.ch/eli/cc/2022/491/en)

### 4.1 Regional Locking: Zurich (`europe-west6`)
All DLP processing resources, API endpoints, and ephemeral storage are cryptographically and geographically bound to the `europe-west6` region.

*   **API Configuration:**
    `"parent": "projects/{project_id}/locations/europe-west6"`
*   **Preventing Transit:** Data transmission occurs exclusively via regional endpoints (`dlp.googleapis.com` rooted in Zurich). The architecture explicitly *disables* global redundancy or failover to other regions (e.g., Frankfurt or US), ensuring packets never traverse non-compliant jurisdictions.

### 4.2 Stateless "RAM-Only" Processing
The system architecture prioritizes ephemerality to minimize the attack surface.
*   **Volatile Memory Inspection:** We utilize the `dlp.content.inspect` method, which streams data directly to the inspection engine's RAM.
*   **Zero-Persistence:** At no point is the unredacted payload written to a disk (SSD/HDD) or stored in a Google Cloud Storage (GCS) bucket. The extensive "Data at Rest" encryption requirements are moot because the data never effectively "rests"—it is processed in flight and returned immediately.

## 5. Environmental Sustainability

While the selection of the Zurich region is primarily mandated by the strict data residency and security requirements detailed above, it inherently aligns with our environmental goals. The choice to host in Switzerland offers a dual advantage: maximum legal protection and minimal carbon impact.

*   **Carbon Free Energy (CFE):** The `europe-west6` (Zurich) region is selected for its exceptional CFE score (approx. **98%**), consistently ranked in the **Global Top 3** of cleanest Google Cloud regions by leveraging the local hydroelectric (~60%) and nuclear (~30%) grid. (Source: [Google Cloud Region CFE Data](https://cloud.google.com/sustainability/region-carbon))
*   **Metrics Integration:** Utilization of the **Google Cloud Carbon Footprint** data (exported to BigQuery) to programmatically generate sustainability reports, validating near-zero carbon execution for the processing workload. (Documentation: [Export to BigQuery](https://docs.cloud.google.com/carbon-footprint/docs/export))

## 6. Technical Benefits

1.  **Risk Mitigation:** Computational verification of manual sanitization provides a demonstrable "best effort" liability shield.
2.  **Structural Integrity:** Translation and reconstruction maintain the original document layout, enabling visual validation against the source.
3.  **Regulatory Compliance:** Hard-coded regional constraints satisfy strict data residency requirements for Swiss healthcare data.
4.  **Optimization:** Pre-filtration of PII and language standardization reduces noise for the downstream extraction models, optimizing the token context window.
