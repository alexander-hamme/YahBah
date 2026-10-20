import { Suspense } from "react";
import type { Metadata } from "next";
import { ReviewWorkspace } from "@/components/review/review-workspace";

export const metadata: Metadata = { title: "Review queue · YahBah" };

export default function ReviewPage() {
  // ReviewWorkspace reads ?run= (useSearchParams), which needs a Suspense boundary to prerender.
  return (
    <Suspense>
      <ReviewWorkspace />
    </Suspense>
  );
}
