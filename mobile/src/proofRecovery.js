/**
 * A lost upload response is not proof of failure. Inspect the server first,
 * then reuse the SAME logical request ID only if retry is required.
 * No image bytes, sessions or personal data are persisted by this module.
 */
export async function inspectOrRetry(readStatus, retrySubmission) {
  const status = await readStatus();
  if (status.state === "completed") {
    return { state: "completed", outcome: status };
  }
  if (status.state === "processing") {
    return { state: "processing" };
  }
  if (status.state !== "retry_required") {
    throw new Error("Unknown proof status");
  }
  return { state: "completed", outcome: await retrySubmission() };
}
