---
title: MooMootScribe
emoji: 💬
colorFrom: yellow
colorTo: purple
sdk: gradio
sdk_version: 5.0.1
app_file: app.py
pinned: false
license: gpl-3.0
short_description: ' AI Content Generator for Moodle Moot DACH talks'
---

# 🎓 MooMoot Scribe

AI Content Generator for Moodle Moot DACH talks with built-in GDPR compliance checking.

## Features

- **Transcription Management**: Upload and manage audio files and transcriptions
- **AI Content Generation**: Generate summaries, social media posts, and metadata
- **Image Generation**: Genreate images based on talk content
- **🔒 GDPR Compliance Checking**: Automatically detects and highlights personal data in transcriptions

## Installation

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the application:

```bash
python demo.py
```

## Usage

1. **Setup Talk**: Create or select a talk project
2. **Upload Transcription**: Upload transcript files with automatic GDPR scanning
3. **Review GDPR Findings**: Check detected personal data and follow recommendations
4. **Generate Content**: Create summaries, social media posts, and images
5. **Export Results**: Save generated content for your Moodle Moot presentation
