import type { UILanguage } from "@/lib/settings";
import type { UploadItem } from "@/lib/types";

export const MAX_IMAGE_BYTES = 10 * 1024 * 1024;
export const MAX_FILE_BYTES = 20 * 1024 * 1024;
export const SUPPORTED_IMAGE_EXTENSIONS = new Set([".png", ".jpg", ".jpeg", ".webp", ".gif"]);
export const SUPPORTED_FILE_EXTENSIONS = new Set([".txt", ".md", ".markdown", ".pdf", ".docx"]);

export function formatFileSize(size: number) {
  if (size < 1024) {
    return `${size} B`;
  }
  if (size < 1024 * 1024) {
    return `${(size / 1024).toFixed(1)} KB`;
  }
  return `${(size / (1024 * 1024)).toFixed(1)} MB`;
}

export function attachmentKindLabel(kind: string, uiLanguage: UILanguage) {
  if (uiLanguage === "en-US") {
    return kind === "image" ? "Image" : "File";
  }

  return kind === "image" ? "图片" : "文件";
}

export function isImageAttachment(attachment: UploadItem) {
  if (attachment.kind === "image") {
    return true;
  }

  const mimeType = attachment.mime_type ?? "";
  return mimeType.startsWith("image/");
}

export function buildAttachmentUrl(storageKey: string) {
  return `/api/backend/uploads/file?storage_key=${encodeURIComponent(storageKey)}`;
}

export function fileExtension(fileName: string) {
  const index = fileName.lastIndexOf(".");
  return index >= 0 ? fileName.slice(index).toLowerCase() : "";
}

export function classifyClientFile(file: File) {
  const extension = fileExtension(file.name);
  if (file.type.startsWith("image/") || SUPPORTED_IMAGE_EXTENSIONS.has(extension)) {
    return "image";
  }
  if (SUPPORTED_FILE_EXTENSIONS.has(extension)) {
    return "file";
  }
  return null;
}

export function cloneUploadItems(items: UploadItem[]) {
  return items.map((item) => ({ ...item }));
}

export function isPdfAttachment(attachment: UploadItem) {
  const fileName = attachment.file_name.toLowerCase();
  return attachment.mime_type === "application/pdf" || fileName.endsWith(".pdf");
}
