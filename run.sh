#!/bin/bash

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "Python 3 is not installed. Please install Python 3.10+"
    exit 1
fi

# Check Git
if ! command -v git &> /dev/null; then
    echo "Git is not installed. Please install Git."
    exit 1
fi

# Install Dependencies
echo "Installing dependencies..."
pip3 install -r requirements.txt || pip install -r requirements.txt
if [ $? -ne 0 ]; then
    echo "Failed to install dependencies."
    exit 1
fi

# Run Dashboard
echo "Starting Dashboard..."
streamlit run dashboard.py
