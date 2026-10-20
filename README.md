# AMD AI League Journey

A central engineering repository documenting our participation and solutions across the **AMD AI League** (hosted via the [Lablab AI Academy Challenge](https://lablab.ai/ai-hackathons/amd-lablab-ai-academy-challenge)). This workspace tracks the progression from foundational ROCm setup to building high-performance, containerized autonomous systems running on AMD Instinct™ accelerators.

---

## Overview

The AMD AI League is a six-stage competitive tournament designed to benchmark real-world AI applications on AMD hardware. Every stage presents a distinct challenge requiring specialized pipelines, strict evaluation contracts, and efficient hardware utilization under tight latency and memory constraints. 

This repository serves as the unified monorepo for tracking research, architecture contracts, local benchmarks, and production-grade Docker containers submitted throughout the League. Each subproject is self-contained with its own dependencies, entrypoints, and automated test suites, ensuring complete reproducibility across local workstations and AMD Developer Cloud environments (such as MI300X instances).

Across all matches, the core engineering philosophy emphasizes:
- **Hardware-Native Execution**: Full compatibility with AMD ROCm™ PyTorch base images without NVIDIA CUDA dependencies.
- **Deterministic Reliability**: Refusal-over-hallucination contracts, token-boundary validation, and strict negative citation guarantees.
- **Production Packaging**: Zero-network container grading compliance, sub-30-second query budgets, and uncompressed image footprints under 60 GiB.

---

## League Matches & Repository Layout

Each match addresses an enterprise domain problem and is maintained within its dedicated directory:

```text
AMD-ACADEMY-CHALLENGE/
├── Retrieval-Augmented-Generation/            # Match 3: Document RAG & Citations
├── Intelligent-Web-Information-Retrieval/     # Match 4: Autonomous Web Agent
├── (Upcoming Match Modules)                   # Matches 5 & 6
└── README.md
```

### 1. Match 1: Foundation & Onboarding
- **Focus**: AMD developer ecosystem onboarding, ROCm toolkit verification, and GPU telemetry validation.
- **Core Milestone**: Establishing baseline developer environments and configuring AMD cloud notebook instances.

### 2. Match 2: Optical Character Recognition (OCR)
- **Focus**: Extracting dense structured data from images, schematics, and photographic labels.
- **Core Milestone**: Multimodal text extraction pipelines leveraging vision backends accelerated on AMD GPUs.

### 3. Match 3: Retrieval-Augmented Generation
- **Directory**: [`Retrieval-Augmented-Generation/`](Retrieval-Augmented-Generation/)
- **Focus**: High-precision RAG over uncurated corporate corpora (PDF, DOCX, XLSX, CSV, code, logs, and images).
- **Core Milestone**: Strict two-phase evaluation (`--index` startup pass and per-query process invocations) with exact citation set matching and zero hallucinated retrievals.

### 4. Match 4: Intelligent Web Information Retrieval
- **Directory**: [`Intelligent-Web-Information-Retrieval/`](Intelligent-Web-Information-Retrieval/)
- **Focus**: Autonomous web navigation and deterministic factual retrieval from live or simulated internet environments.
- **Core Milestone**: A 5-stage agent pipeline (Understand → Search → Fetch → Extract → Verify) enforcing token-boundary proof before returning answers.

### 5. Match 5: AI-Powered Code Repository Repair
- **Focus**: Multi-agent software engineering systems for static analysis, automated defect triage, and patch synthesis.
- **Core Milestone**: Autonomous codebase localization, automated test running, and minimal-diff defect resolution.

### 6. Match 6: Can Your AI Master the Unknown?
- **Focus**: Reinforcement learning and exploratory agent decision-making in hidden, unfamiliar environments.
- **Core Milestone**: Efficient state-space navigation and mission completion while optimizing compute tokens and GPU inference cost.

---

## Environment & Tooling

- **Platform**: Linux (Ubuntu 22.04+)
- **Compute**: AMD Instinct™ Accelerators (ROCm 6.x / PyTorch ROCm releases)
- **Evaluation**: Headless Docker container execution (`--network none`, non-root capabilities)
