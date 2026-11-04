# Use the official Python image
FROM python:3.11-slim

# Set the working directory
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the project code
COPY . .

# Start the application using Gunicorn
# This matches your Procfile logic
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 run:app