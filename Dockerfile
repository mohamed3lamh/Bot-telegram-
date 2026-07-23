# نستخدم إصدار Debian Bullseye (11) لأنه يحتوي افتراضياً على مكتبة libssl.so.1.1 المطلوبة لـ TDLib
FROM python:3.11-slim-bullseye

# متغيرات البيئة لتحسين أداء البايثون
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# تحديد مجلد العمل
WORKDIR /app

# تثبيت المتطلبات الأساسية للنظام إن وجدت
RUN apt-get update && apt-get install -y \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# نسخ ملف المتطلبات وتثبيت مكتبات البايثون
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# نسخ باقي ملفات المشروع
COPY . .

# الأمر الافتراضي لتشغيل البوت
CMD ["python3", "-O", "main_bot.py"]
