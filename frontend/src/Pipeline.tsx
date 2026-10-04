import {
  Check,
  Compass,
  Code2,
  Layers,
  MessageSquare,
  Play,
  ShieldCheck,
} from "lucide-react";

import type { LucideIcon } from "lucide-react";

type PipelineProps = {
  activeIndex: number;
  finished: boolean;
};

const steps: { label: string; icon: LucideIcon }[] = [
  { label: "Question", icon: MessageSquare },
  { label: "DB Routing", icon: Compass },
  { label: "RAG", icon: Layers },
  { label: "SQL", icon: Code2 },
  { label: "Validation", icon: ShieldCheck },
  { label: "Execution", icon: Play },
];

export default function Pipeline({ activeIndex, finished }: PipelineProps) {
  return (
    <div className="pipeline">
      {steps.map((step, index) => {
        let state = "pending";

        if (finished || index < activeIndex) {
          state = "done";
        } else if (index === activeIndex) {
          state = "active";
        }

        const Icon = step.icon;

        return (
          <div className="pipeline-item" key={step.label}>
            <div className={`pipeline-step ${state}`}>
              <div className="pipeline-icon">
                <Icon size={17} />
                {state === "done" && (
                  <span className="pipeline-tick">
                    <Check size={9} strokeWidth={4} />
                  </span>
                )}
              </div>

              <span className="pipeline-label">{step.label}</span>

              <span className="pipeline-state">
                {state === "done"
                  ? "Done"
                  : state === "active"
                  ? "Running"
                  : "Waiting"}
              </span>
            </div>

            {index < steps.length - 1 && (
              <div
                className={
                  state === "done"
                    ? "pipeline-line filled"
                    : "pipeline-line"
                }
              />
            )}
          </div>
        );
      })}
    </div>
  );
}