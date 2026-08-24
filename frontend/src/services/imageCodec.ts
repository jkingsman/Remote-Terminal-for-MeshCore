import type { ImageFormatId } from '../utils/imageEnvelope';

export interface EncodedMeshImage {
  blob: Blob;
  format: ImageFormatId;
  width: number;
  height: number;
}

const ACCEPTED_TYPES = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/avif']);

function canvasBlob(canvas: HTMLCanvasElement, type: string, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, type, quality));
}

export async function encodeMeshImage(file: File, maxDimension: 64 | 128 | 256 = 256) {
  if (!ACCEPTED_TYPES.has(file.type)) throw new Error('Choose a JPEG, PNG, WebP, or AVIF image.');
  const source = await createImageBitmap(file);
  const scale = Math.min(1, maxDimension / Math.max(source.width, source.height));
  const width = Math.max(1, Math.round(source.width * scale));
  const height = Math.max(1, Math.round(source.height * scale));
  const canvas = document.createElement('canvas');
  canvas.width = width;
  canvas.height = height;
  const context = canvas.getContext('2d', { alpha: false });
  if (!context) throw new Error('This browser cannot prepare images.');
  context.drawImage(source, 0, 0, width, height);
  source.close();

  const pixels = context.getImageData(0, 0, width, height);
  for (let offset = 0; offset < pixels.data.length; offset += 4) {
    const luminance = Math.round(
      pixels.data[offset] * 0.299 +
        pixels.data[offset + 1] * 0.587 +
        pixels.data[offset + 2] * 0.114
    );
    pixels.data[offset] = luminance;
    pixels.data[offset + 1] = luminance;
    pixels.data[offset + 2] = luminance;
    pixels.data[offset + 3] = 255;
  }
  context.putImageData(pixels, 0, 0);

  const avif = await canvasBlob(canvas, 'image/avif', 0.2);
  if (avif && avif.type === 'image/avif') return { blob: avif, format: 0, width, height } as const;
  const jpeg = await canvasBlob(canvas, 'image/jpeg', 0.35);
  if (!jpeg) throw new Error('This browser cannot encode the selected image.');
  return { blob: jpeg, format: 1, width, height } as const;
}
