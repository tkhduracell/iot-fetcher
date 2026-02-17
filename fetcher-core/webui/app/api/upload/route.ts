import { NextRequest, NextResponse } from 'next/server';
import { randomUUID } from 'crypto';
import { writeFile, mkdir, stat } from 'fs/promises';
import path from 'path';

const UPLOAD_DIR = '/tmp/uploads';

export async function POST(request: NextRequest) {
  const formData = await request.formData();
  const file = formData.get('file') as File | null;

  if (!file) {
    return new NextResponse('No file part', { status: 400 });
  }

  await mkdir(UPLOAD_DIR, { recursive: true });

  const now = new Date();
  const hhmm = now.toTimeString().slice(0, 5).replace(':', '');
  const uuidName = `${randomUUID()}-${hhmm}`;
  const filepath = path.join(UPLOAD_DIR, uuidName);

  const buffer = Buffer.from(await file.arrayBuffer());
  await writeFile(filepath, buffer);

  const stats = await stat(filepath);
  const sizeMb = Math.round((stats.size / (1024 * 1024)) * 100) / 100;

  const createdAt = new Date().toLocaleString('sv-SE', { timeZone: 'Europe/Stockholm' });

  return NextResponse.json({
    name: uuidName,
    created_at: createdAt,
    size_mb: sizeMb,
  });
}
