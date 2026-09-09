import type { Locale } from "../i18n/messages";

export function formatMoney(value: string | number, locale: Locale): string {
  return new Intl.NumberFormat(locale === "ar" ? "ar-EG" : "en-EG", {
    style: "currency",
    currency: "EGP",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  }).format(Number(value));
}

export function formatDateTime(value: string, locale: Locale): string {
  return new Intl.DateTimeFormat(locale === "ar" ? "ar-EG" : "en-EG", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Africa/Cairo",
  }).format(new Date(value));
}

export function formatBusinessDate(value: string, locale: Locale): string {
  const [year, month, day] = value.split("-").map(Number);
  return new Intl.DateTimeFormat(locale === "ar" ? "ar-EG" : "en-EG", {
    dateStyle: "full",
    timeZone: "Africa/Cairo",
  }).format(new Date(Date.UTC(year, month - 1, day, 12)));
}

export function lineTotal(price: string, quantity: number): number {
  return Math.round(Number(price) * quantity * 100) / 100;
}
