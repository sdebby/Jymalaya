// The Ask coach mark (2026-11-14, the athlete's pick of five): two speech
// bubbles, a back-and-forth with the coach. Stroke only, in currentColor, so
// it takes the colour of the button or label it sits in.
export function ChatIcon({ size = 22, stroke = 1.9 }: { size?: number; stroke?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={stroke}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{ display: "block" }}
    >
      <path d="M3 4h12v8H7l-4 3z" />
      <path d="M18 8h3v10l-4-3H9v-3" />
    </svg>
  );
}
