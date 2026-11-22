/**
 * Pure approval-decision logic — no VS Code dependency.
 *
 * The approval flow is:
 *  1. generation completes and the preview is ready
 *  2. a native diff is shown (extension-side)
 *  3. the user approves or rejects (modal)
 *  4. on approval: re-read the file and compare against the baseline
 *  5. write only if the file was not modified externally (or the user
 *     explicitly chooses "Apply anyway")
 */

export type ApprovalDecision = "apply" | "reject" | "cancel";

/**
 * Map the modal dialog choice onto an approval decision.
 * "Apply"/"Apply changes" → apply; "Reject" → reject; anything else
 * (dismiss, undefined) → cancel (no changes).
 */
export function mapApprovalChoice(choice: string | undefined): ApprovalDecision {
    if (choice === "Apply" || choice === "Apply changes" || choice === "Apply anyway" || choice === "Overwrite") {
        return "apply";
    }
    if (choice === "Reject" || choice === "Reject changes" || choice === "Reject") {
        return "reject";
    }
    return "cancel";
}

/** Map the conflict dialog choice onto an approval decision. */
export function mapConflictChoice(choice: string | undefined): ApprovalDecision {
    if (choice === "Apply anyway" || choice === "Overwrite") return "apply";
    return "cancel";
}

export interface ApprovalPlan {
    decision: ApprovalDecision;
    reason: string;
}

/**
 * Given the baseline (state captured at generation start), the current state
 * (read at approval time), and the two dialogs' answers, decide what happens
 * to the proposed content.
 *
 * @returns "apply" to write the file, otherwise the outcome that must leave
 * the original file untouched.
 */
export function decideApprovalOutcome(args: {
    baselineExists: boolean;
    baselineContent: string | null;
    currentExists: boolean;
    currentContent: string;
    approvalChoice: ApprovalDecision;
    /** Conflict choice ("apply" / "cancel"), only consulted when a conflict exists. */
    conflictChoice: ApprovalDecision | null;
}): ApprovalPlan {
    const { baselineExists, baselineContent, currentExists, currentContent, approvalChoice, conflictChoice } = args;

    if (approvalChoice !== "apply") {
        return {
            decision: approvalChoice === "reject" ? "reject" : "cancel",
            reason: approvalChoice === "reject" ? "The user rejected the changes." : "The approval dialog was dismissed.",
        };
    }

    // The proposed content equals the session's previewed content; the
    // workspace file must have not changed since generation started.
    if (!baselineExists && currentExists) {
        // New file was created externally while generating.
        if (conflictChoice === "apply") {
            return { decision: "apply", reason: "The file was created externally; overwriting it was confirmed." };
        }
        return { decision: "cancel", reason: "The file was created externally since generation started." };
    }

    if (baselineExists && !currentExists) {
        if (conflictChoice === "apply") {
            return { decision: "apply", reason: "The file was deleted externally; creating it was confirmed." };
        }
        return { decision: "cancel", reason: "The file was deleted externally since generation started." };
    }

    if (baselineExists && currentExists && baselineContent !== currentContent) {
        if (conflictChoice === "apply") {
            return { decision: "apply", reason: "The file was modified since generation started; overwriting it was confirmed." };
        }
        return { decision: "cancel", reason: "The file was modified since generation started." };
    }

    return { decision: "apply", reason: "No external modifications detected." };
}
