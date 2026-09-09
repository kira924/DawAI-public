import { BrandMark } from "./BrandMark";

export function LoadingScreen() {
  return (
    <main className="loading-screen" aria-busy="true">
      <BrandMark />
      <span className="spinner" aria-hidden="true" />
    </main>
  );
}
