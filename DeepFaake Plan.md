VIDEO INPUT │ ▼ ┌──────────────────┐ │ Video Preprocess │ │ FFmpeg + OpenCV  │ └────────┬─────────┘ │ ▼ FRAME SAMPLING │ ▼ ┌───────────────┐ │ FACE DETECTOR │ │ SCRFD/RetinaFace └───────┬───────┘ │ ▼ FACE ALIGNMENT │ ┌───────────────┼────────────────┐ │               │                │ ▼               ▼                ▼ SPATIAL BRANCH   TEMPORAL BRANCH   FREQUENCY BRANCH │               │                │ ▼               ▼                ▼ ConvNeXt-Tiny     VideoMAE /       FFT/DCT / Xception        Transformer       features │               │                │ └───────────────┼────────────────┘ ▼ FEATURE FUSION │ ▼ CLASSIFIER │ ┌─────────┴─────────┐ ▼                   ▼ FAKE / REAL          CONFIDENCE │ ┌────────┼─────────┐ ▼        ▼         ▼ Spatial  Temporal  Frequency Evidence Evidence  Evidence │        │         │ └────────┼─────────┘ ▼ EXPLAINABILITY 

Grad-CAM + Timeline │ ▼ FORENSIC REPORT 

That is the architecture I'd actually build. 

# **2. How many models?** 

This is where I want to simplify things. 

## **Our system has approximately 5 learned components** 

**Componen What it does Model t** 1 Detect faces **SCRFD / RetinaFace** 2 Spatial manipulation detection **ConvNeXt-Tiny / Xception** 3 Temporal manipulation detection **VideoMAE-based Transformer** 4 Frequency anomaly detection **Small MLP** over FFT/DCT features 5 Combine evidence **Fusion MLP / attention layer** 

But importantly: 

### **You don't have to train five huge models.** 

Only the actual deepfake detector branches need serious training. 

# **3. Model 1 — Face Detection** 

**Recommended:** 

#### **SCRFD** 

or 

#### **RetinaFace** 

I'd start with **SCRFD** because it's fast enough for a practical pipeline. 

Its job is NOT to detect deepfakes. 

It simply answers: 

"Where are the faces?" 

Example: 

Frame 001 │ ▼ ┌─────────────────────────┐ │                         │ │       ┌─────────┐       │ │       │  FACE   │       │ │       └─────────┘       │ │                         │ └─────────────────────────┘ 

Then we crop the face. 

# **4. Face alignment** 

This doesn't need another ML model. 

Use facial landmarks from the detector. 

For example: 

left eye right eye nose left mouth right mouth 

Then transform the face into a standardized orientation. Original face ↓ landmarks ↓ alignment ↓ 224 × 224 face crop 

This is extremely important. 

Because our detector should learn: 

"Is this face manipulated?" 

not: 

"Is this person's head tilted?" 

# **5. Model 2 — Spatial detector** 

This is our first **actual deepfake detector** . 

I'd choose: 

### **ConvNeXt-Tiny** 

instead of building a CNN from scratch. 

Alternative: 

### **Xception** 

Xception has historically been extremely common in face-forensics research. 

The spatial model looks at **individual frames** . 

For example: 

FRAME 

↓ FACE ↓ ConvNeXt 

↓ feature vector ↓ spatial manipulation probability 

It can learn artifacts such as: 

- unnatural skin texture 

- blending boundaries 

- facial inconsistencies 

- eyes/mouth artifacts 

- warping 

- inconsistent lighting 

- resolution artifacts 

# **6. Model 3 — Temporal detector** 

This is where I would **update your original 2024 architecture** . 

Originally you had: 

CNN + LSTM 

I would **not make LSTM the centerpiece in 2026** . 

Instead: 

### **VideoMAE / Video Transformer-style temporal model** 

The idea is: 

Frame 1 Frame 2 Frame 3 Frame 4 Frame 5 ↓ Temporal model ↓ temporal representation 

It learns relationships across frames. 

Why? 

Because a face swap might look perfectly reasonable in one frame but behave strangely across time. 

For example: 

Frame 1       normal Frame 2       normal Frame 3       eyebrow artifact Frame 4       mouth boundary shifts Frame 5       normal 

Frame 6       eye shape changes 

A spatial model might miss this. 

A temporal model can notice: 

"Something isn't consistent over time." 

# **7. Model 4 — Frequency branch** 

This one is important because it gives your project a **forensic angle** . 

Instead of another giant neural network: 

Image ↓ FFT ↓ frequency spectrum ↓ statistical features 

We look for unusual high-frequency patterns introduced by: 

- resizing 

- interpolation 

- blending 

- generation 

- compression 

- synthesis 

You can calculate: 

FFT DCT spectral energy high-frequency energy frequency distribution 

Then feed those features into a small: 

### **MLP classifier** 

So: 

Face 

↓ FFT / DCT ↓ frequency features ↓ MLP ↓ frequency anomaly score 

This is much more realistic than claiming we're training another enormous neural network. 

# **8. The most important part: Feature Fusion** 

Now we have: 

Spatial score       = 0.93 Temporal score      = 0.87 Frequency score     = 0.76 

We don't simply average them. 

We create: 

Spatial features + Temporal features + Frequency features ↓ Feature Fusion ↓ Final classifier For example: 

┌───────────────┐ Spatial ────────►│               │ │               │ Temporal ───────►│ Feature Fusion├──► Final probability │               │ Frequency ──────►│               │ └───────────────┘ 

The fusion layer learns how much each type of evidence should contribute. 

# **9. Example final result** 

Suppose the video contains a face swap. 

The backend might produce: 

{ "verdict": "LIKELY_MANIPULATED", "confidence": 0.947, "spatial_score": 0.93, "temporal_score": 0.87, "frequency_score": 0.76, "suspicious_segments": [ { "start": 12.4, "end": 15.8, "score": 0.96 } ] } 

Your frontend turns that into: 

┌─────────────────────────────────────────────┐ │                                             │ │ ⚠ LIKELY MANIPULATED              │ │                                             │ │                  94.7%                      │ │                                             │ ├─────────────────────────────────────────────┤ │                                             │ │ SPATIAL       TEMPORAL       FREQUENCY      │ │  93%            87%             76%         │ │                                             │ ├─────────────────────────────────────────────┤ │                                             │ │ VIDEO                                       │ │ ┌─────────────────────────────────────────┐ │ │ │              FACE                       │ │ │ │            🔥 HEATMAP                   │ │ │ └─────────────────────────────────────────┘ │ │                                             │ ├─────────────────────────────────────────────┤ 

│ SUSPICIOUS TIMELINE                         │ │ ────────████████───────██████────────       │ │         12.4s           15.8s               │ └─────────────────────────────────────────────┘ 

This is where your **crazy frontend** actually becomes meaningful. 

# **10. Explainability** 

We don't need another model here. 

Use: 

### **Grad-CAM** 

For the spatial model. 

It answers: 

"Which region of the face influenced the prediction?" 

So instead of: 

AI says fake: 94% 

we can show: 

FACE 

┌──────────────┐ │   👁 🔥      │ │      🔥      │ │   🔥 👄      │ └──────────────┘ 

The investigator can see **where the model found suspicious evidence** . 

That is much stronger for a forensic application. 

# **11. Suspicious timeline** 

Again, **no extra ML model necessarily required** . 

If each sampled frame has: 

Frame 001 → 0.12 Frame 002 → 0.18 Frame 003 → 0.21 ... Frame 300 → 0.94 Frame 301 → 0.97 Frame 302 → 0.95 

we plot: 

Manipulation score 

1.0 ┤                    ███ │                    ███ 0.8 ┤                   █████ │              █████████ 

0.6 ┤          █████████████ │ 0.4 ┤ │ 0.2 ┤████████ └────────────────────────── time → 

Then identify suspicious intervals. 

# **12. What about audio?** 

The original PS mentions audio-visual inconsistencies as **one possible approach** , but this is specifically: 

#### **face-swap deepfake video detection** 

#### Therefore I would **not put audio detection into V1** . 

You can add it later. 

If you have time: 

Audio 

↓ Whisper / speech features ↓ Lip movement 

↓ audio-visual consistency 

But don't let this explode the project. 

# **13. What about blockchain?** 

For **SIH1683** , blockchain is not necessary for the core detector. 

Don't force blockchain into this just because blockchain sounds impressive. 

Your actual problem is: 

Video ↓ Detection ↓ Forensic evidence ↓ Report 

However, you can add a **provenance/integrity layer** later: 

Video 

↓ SHA-256 hash ↓ Report hash ↓ Blockchain 

This allows you to say: 

"This report corresponds to exactly this video artifact." 

That's useful for chain-of-custody. 

But I'd call it an **optional forensic integrity module** , not the deepfake detector itself. 

# **14. Database architecture** 

Now the important part. 

## **Don't store videos directly in PostgreSQL.** 

Use: 

### **Object storage** 

#### **MinIO** 

or 

#### **AWS S3** 

for: 

original video extracted frames face crops heatmaps generated reports Then use: 

### **PostgreSQL** 

for structured information. 

Architecture: 

BACKEND │ ┌────────────┴────────────┐ │                         │ ▼                         ▼ PostgreSQL                MinIO/S3 │                         │ │                    ┌────┴────┐ │                    │         │ metadata              videos    frames results               crops     heatmaps users                 reports jobs 

# **15. PostgreSQL tables** 

You don't need 30 tables. 

Start with: 

### **users** 

id name email created_at 

### **investigations** 

id user_id video_id status created_at completed_at 

### **videos** 

id investigation_id filename storage_path sha256 duration fps resolution created_at 

### **analysis_results** 

id investigation_id 

final_score verdict 

spatial_score temporal_score frequency_score 

model_version created_at 

### **evidence** 

id investigation_id 

timestamp frame_number evidence_type score heatmap_path description 

### **reports** 

id investigation_id report_path report_hash created_at 

That's enough for V1. 

# **16. Redis — do we need it?** 

Eventually: 

#### **Yes.** 

But not on day one. 

Video analysis is computationally expensive. 

You don't want: 

POST /analyze 

↓ 

FastAPI waits 4 minutes ↓ browser timeout Instead: 

POST /analyze ↓ Create job ↓ Redis queue ↓ GPU worker 

↓ Analysis ↓ PostgreSQL ↓ Frontend receives result Architecture: React │ ▼ FastAPI │ ▼ Redis Queue │ ▼ ML Worker │ ├── Face Detection ├── Spatial ├── Temporal ├── Frequency └── Fusion │ ▼ PostgreSQL │ ▼ MinIO 

For development, you can initially run the analysis synchronously and introduce Celery/RQ + Redis once the pipeline works. 

# **17. Complete technology stack** 

This is what I would freeze. 

## **Frontend** 

React TypeScript Tailwind CSS shadcn/ui 

Motion GSAP Recharts / custom visualization Three.js / React Three Fiber 

But remember: 

**Three.js is optional decoration/visualization** , not part of the ML pipeline. 

# **Backend** 

Python FastAPI Pydantic SQLAlchemy PostgreSQL Redis Celery/RQ 

# **Video processing** 

FFmpeg OpenCV PyAV 

# **Face processing** 

SCRFD / RetinaFace facial landmarks alignment 

# **ML** 

PyTorch 

Spatial: ConvNeXt-Tiny / Xception 

Temporal: 

VideoMAE-style video transformer 

Frequency: FFT + DCT + MLP 

Fusion: MLP / attention-based fusion 

# **Explainability** 

Grad-CAM confidence scores frame-level scores temporal anomaly timeline 

# **Storage** 

PostgreSQL + MinIO 

# **Reports** 

HTML PDF JSON 

# **Optional integrity** 

SHA-256 + blockchain anchoring 

# **18. The complete production pipeline** 

This is the diagram I want you to remember. 



<!-- Start of picture text -->
┌──────────────────────────────────────────────────────┐<br>│                    USER                              │<br>└──────────────────────┬───────────────────────────────┘<br>                       │<br>                       ▼<br>                ┌───────────────┐<br>                │ React Frontend│<br>                └───────┬───────┘<br>                        │<br>                  Upload Video<br>                        │<br>                        ▼<br>                ┌───────────────┐<br>                │    FastAPI    │<br>                └───────┬───────┘<br>                        │<br>                        ▼<br>                 Store Original<br>                  MinIO / S3<br>                        │<br>                        ▼<br>                  Create Job<br>                        │<br>                        ▼<br>                  Redis Queue<br>                        │<br>                        ▼<br>              ┌──────────────────┐<br>              │    ML WORKER     │<br>              └────────┬─────────┘<br>                       │<br>                       ▼<br>                 FFmpeg/OpenCV<br>                       │<br>                       ▼<br>                  Frame Sampling<br>                       │<br>                       ▼<br>                  Face Detection<br>                   SCRFD/Retina<br>                       │<br>                       ▼<br>                  Face Alignment<br>                       │<br>          ┌────────────┼─────────────┐<br>          │            │             │<br>          ▼            ▼             ▼<br>      SPATIAL       TEMPORAL      FREQUENCY<br><!-- End of picture text -->

ConvNeXt      VideoMAE       FFT/DCT │            │             │ ▼            ▼             ▼ Features      Features       Features │            │             │ └────────────┼─────────────┘ ▼ FEATURE FUSION │ ▼ FINAL CLASSIFIER │ ┌────────┼─────────┐ ▼        ▼         ▼ Verdict   Evidence  Confidence │        │         │ └────────┼─────────┘ ▼ Grad-CAM Timeline Heatmaps │ ▼ FORENSIC REPORT │ ┌────────┴────────┐ ▼                 ▼ PostgreSQL           MinIO │                 │ └────────┬────────┘ ▼ React Dashboard │ ▼ Investigator 

# **19. What the user will actually see** 

Your application can have around **6 major screens** . 

### **1. Landing** 

DEEPTRACE AI-POWERED VIDEO FORENSICS 

[ START INVESTIGATION ] 

This is where your crazy creative frontend comes in. 

### **2. Investigation Dashboard** 

My Investigations 

INV-001 INV-002 INV-003 

### **3. Upload** 

┌─────────────────────────────┐ │                             │ │     DROP VIDEO HERE         │ │                             │ │     MP4 / MOV / AVI         │ │                             │ └─────────────────────────────┘ 

[ START ANALYSIS ] 

### **4. Processing** 

This is where we make the pipeline visually impressive: 

VIDEO INGESTION       ✓ FRAME EXTRACTION      ✓ FACE DETECTION        ✓ SPATIAL ANALYSIS      ● TEMPORAL ANALYSIS     ○ FREQUENCY ANALYSIS    ○ FEATURE FUSION        ○ 

### **5. Investigation Result** 

The **most important screen** : 

LIKELY MANIPULATED 

┌────────┬────────┬────────┐ │Spatial │Temporal│Frequency 

│  93%   │  87%   │  76%   │ └────────┴────────┴────────┘ 

VIDEO + HEATMAP 

────────████████████────────────── 12.4s → 15.8s 

[ VIEW EVIDENCE ] [ GENERATE REPORT ] 

### **6. Forensic Report** 

FORENSIC ANALYSIS REPORT 

Video Information Hash Duration Resolution FPS 

Final Verdict 

Model Evidence 

Spatial Analysis Temporal Analysis Frequency Analysis 

Suspicious Frames 

Heatmaps 

Model Information 

[ EXPORT PDF ] [ EXPORT JSON ] 

# **20. One important change from your original diagram** 

Your original diagram was: 

CNN 

+ 

LSTM + FFT 

#### That's a good **2024 conceptual architecture** . 

Our updated architecture is: 

Face Detector ↓ Alignment ↓ ┌──────────────┬──────────────┬──────────────┐ │              │              │ ▼              ▼              ▼ Spatial       Temporal       Frequency ConvNeXt      VideoMAE       FFT/DCT │              │              │ └──────────────┼──────────────┘ ▼ Feature Fusion ▼ Final Classifier ▼ Explainability + Report 

This is the architecture I'd present in your **2026 SIH proposal** . 

# **21. But don't train everything from scratch** 

#### This is **extremely important** . 

You're a CSE student building an SIH prototype. 

Don't attempt: 

Train CNN from zero + Train Transformer from zero + Train face detector + Train frequency network 

That's a research lab project. 

Instead: 

Pretrained models 

↓ Fine-tune on deepfake datasets 

↓ Evaluate 

↓ Fusion 

Your research contribution becomes: 

#### **A hybrid forensic detection framework that combines spatial facial artifacts, temporal inconsistencies and frequency-domain evidence with explainable localization.** 

That's a much more defensible SIH story. 

# **22. Datasets** 

For training/evaluation, you'll want established deepfake datasets rather than generating everything yourself. 

The important ones to investigate are: 

FaceForensics++ Celeb-DF DFDC DeeperForensics-1.0 WildDeepfake 

And particularly look into **DeepfakeBench** , which provides a common benchmark/framework for deepfake detection research. 

Your eventual experiment should look something like: 

Train ↓ FaceForensics++ ↓ Validation ↓ Celeb-DF ↓ 

Test ↓ 

Unseen / cross-dataset videos 

The last part matters because a detector that scores 99% on its training distribution but collapses on new manipulation methods isn't actually robust. 

# **23. Your final "number of things"** 

If you want a clean answer for your team: 

### **ML components** 

#### **5 learned components** 

1. Face detector 

2. Spatial detector 

3. Temporal detector 

4. Frequency MLP 

5. Fusion classifier 

#### But only **3 are your actual forensic evidence branches** : 

SPATIAL TEMPORAL FREQUENCY 

### **Databases** 

#### **1 database** 

PostgreSQL 

### **Object storage** 

#### **1** 

MinIO 

### **Cache/queue** 

#### **1** 

Redis 

### **Backend** 

#### **1** 

FastAPI 

### **Frontend** 

#### **1** 

React + Tailwind + shadcn 

### **Video processing** 

FFmpeg + OpenCV 

### **Explainability** 

Grad-CAM + temporal visualization 

### **Optional** 

SHA-256 Blockchain provenance Audio-visual analysis 

# **24. And this is the order I want you to build it** 

#### **Do NOT start by building all of this.** 

Build in this exact order: 

PHASE 1 Video upload ↓ FFmpeg ↓ Frame extraction ↓ Face detection ↓ Display detected faces 

↓ 

PHASE 2 

Face crops ↓ Spatial model ↓ Fake/real prediction 

↓ 

PHASE 3 Multiple frames ↓ Temporal model ↓ Temporal score 

↓ 

PHASE 4 FFT/DCT ↓ Frequency features ↓ Frequency score 

↓ 

PHASE 5 Spatial + Temporal + Frequency ↓ Fusion ↓ Final prediction 

↓ 

PHASE 6 Grad-CAM + frame scores + timeline ↓ Evidence 

↓ 

PHASE 7 PostgreSQL + MinIO + Redis ↓ Real backend 

↓ PHASE 8 React dashboard ↓ Figma design ↓ Tailwind/shadcn ↓ Motion/GSAP ↓ Three.js where useful 

↓ 

PHASE 9 PDF forensic report ↓ SHA-256 ↓ Optional blockchain 

**This is the architecture I would lock in now.** Don't add more models unless an experiment shows that one of these branches is insufficient. The strength of your project won't come from saying _"we used seven AI models"_ ; it'll come from showing that **three different forensic signals independently detect manipulation, their fusion improves robustness, and the system can explain where and why it made its decision.** 

