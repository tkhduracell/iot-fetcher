import { NextRequest, NextResponse } from 'next/server';
import { readFile, stat } from 'fs/promises';
import path from 'path';

const UPLOAD_DIR = '/tmp/uploads';

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ uuid: string }> }
) {
  const { uuid } = await params;
  const filepath = path.join(UPLOAD_DIR, uuid);

  try {
    const fileBuffer = await readFile(filepath);
    const fileStat = await stat(filepath);

    return new NextResponse(fileBuffer, {
      status: 200,
      headers: {
        'Content-Length': fileStat.size.toString(),
        'Content-Disposition': `attachment; filename="${uuid}"`,
      },
    });
  } catch {
    return new NextResponse('File not found', { status: 404 });
  }
}

export async function HEAD(
  request: NextRequest,
  { params }: { params: Promise<{ uuid: string }> }
) {
  const { uuid } = await params;
  const filepath = path.join(UPLOAD_DIR, uuid);

  try {
    const fileStat = await stat(filepath);

    return new NextResponse(null, {
      status: 200,
      headers: {
        'Content-Length': fileStat.size.toString(),
      },
    });
  } catch {
    return new NextResponse(null, { status: 404 });
  }
}
