// Small formatting helpers shared across components.

export const km = (m?: number | null) => (m == null ? "—" : (m / 1000).toFixed(1));

export function hm(sec?: number | null): string {
  if (!sec) return "0:00";
  const totalMin = Math.round(sec / 60); // round first, then split, so 59.7m -> 1:00 not 0:60
  const h = Math.floor(totalMin / 60);
  const m = totalMin % 60;
  return `${h}:${m.toString().padStart(2, "0")}`;
}

export function pace(secPerUnit?: number | null): string {
  if (!secPerUnit) return "—";
  const total = Math.round(secPerUnit); // round first, then split, so 119.7s -> 2:00 not 1:60
  const m = Math.floor(total / 60);
  const s = total % 60;
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Bikes are read as speed, not pace — derived from the stored seconds-per-km.
export function speedKmh(secPerKm?: number | null): string {
  if (!secPerKm) return "—";
  return (3600 / secPerKm).toFixed(1);
}

export const round = (v?: number | null, d = 0) => (v == null ? "—" : v.toFixed(d));

// Completed years since an ISO birthday, or null when the date is unparseable,
// in the future, or absurdly old. Mirrors age_on() in app/config.py, and builds
// the Date from parts so it is a local midnight -- `new Date("1988-06-15")` is
// UTC midnight, which reads as the day before west of UTC and can shift the age
// by a year on a birthday.
export function ageFromBirthday(iso?: string | null): number | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec((iso ?? "").trim());
  if (!m) return null;
  const [y, mo, d] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const bd = new Date(y, mo - 1, d);
  if (bd.getFullYear() !== y || bd.getMonth() !== mo - 1 || bd.getDate() !== d) return null;
  const today = new Date();
  const beforeBirthday =
    today.getMonth() < mo - 1 || (today.getMonth() === mo - 1 && today.getDate() < d);
  const age = today.getFullYear() - y - (beforeBirthday ? 1 : 0);
  return age < 0 || age > 120 ? null : age;
}

// A Date is accepted as well as an ISO string: `new Date("2026-08-26")` is
// parsed as UTC midnight and rendered locally, which shifts the label a day west
// of UTC. Callers holding a real local Date should pass it straight through.
export function monthDay(iso: string | Date): string {
  const d = iso instanceof Date ? iso : new Date(iso);
  if (isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}
