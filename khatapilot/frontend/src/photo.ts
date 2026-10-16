// Invoice photos: shrink a big phone photo, send it to be read, and get the text back for the owner to check.

import { api } from "./api";

export interface PhotoReading {
  readable: boolean;
  text: string;
  truncated: boolean;
  message: string;
  model: string;
}

/** Photos at or under this size go as they are (the offline demo knows its sample photos byte for byte). */
const SEND_AS_IS = 1.5 * 1024 * 1024;
const MAX_SIDE = 2000;

async function shrink(photo: Blob): Promise<Blob> {
  if (photo.size <= SEND_AS_IS) return photo;
  const bitmap = await createImageBitmap(photo);
  const scale = Math.min(1, MAX_SIDE / Math.max(bitmap.width, bitmap.height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bitmap.width * scale);
  canvas.height = Math.round(bitmap.height * scale);
  canvas.getContext("2d")!.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
  bitmap.close();
  return new Promise((resolve, reject) =>
    canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error("Could not prepare the photo."))), "image/jpeg", 0.85),
  );
}

function base64(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",", 2)[1] ?? "");
    reader.onerror = () => reject(new Error("Could not read the photo."));
    reader.readAsDataURL(blob);
  });
}

export async function readInvoicePhoto(photo: Blob): Promise<PhotoReading> {
  return api.post<PhotoReading>("/chat/photo", { image_base64: await base64(await shrink(photo)) });
}

/** How many blocks one chat message may forward (the backend's limit). */
export const MAX_FORWARDED = 20;
const AT_ONCE = 3; // photos read in parallel

export interface PhotoResult {
  reading?: PhotoReading;
  error?: unknown;
}

/** Read several photos a few at a time. Results keep the photos' order; `onRead` fires as each one finishes. */
export async function readInvoicePhotos(photos: Blob[], onRead: (done: number) => void): Promise<PhotoResult[]> {
  const results: PhotoResult[] = new Array(photos.length);
  let next = 0;
  let done = 0;
  async function worker() {
    while (next < photos.length) {
      const i = next++;
      try {
        results[i] = { reading: await readInvoicePhoto(photos[i]) };
      } catch (error) {
        results[i] = { error };
      }
      onRead(++done);
    }
  }
  await Promise.all(Array.from({ length: Math.min(AT_ONCE, photos.length) }, worker));
  return results;
}

export const SAMPLE_PHOTOS = [
  { label: "Meghna invoice", url: "/samples/invoice-meghna.png" },
  { label: "Shapla invoice (doctored)", url: "/samples/invoice-shapla.png" },
];
