import { motion, useReducedMotion } from "motion/react";
import { AudioLines, Check, ShieldAlert, Power } from "lucide-react";
import { labels, type AgentStatus } from "./contracts";
export function VoiceOrb({
  status,
  small = false,
  reduce = false,
}: {
  status: AgentStatus;
  small?: boolean;
  reduce?: boolean;
}) {
  const prefers = useReducedMotion();
  const active = !["offline", "emergency_stopped", "failed"].includes(status);
  const animate = active && !prefers && !reduce;
  return (
    <div
      className={`orb-wrap ${small ? "small" : ""} state-${status}`}
      role="img"
      aria-label={labels[status]}
      data-motion={animate ? "on" : "off"}
    >
      <div className="orb-grid" />
      <motion.div
        className="orb-halo"
        animate={
          animate
            ? { scale: [1, 1.045, 1], opacity: [0.45, 0.7, 0.45] }
            : { scale: 1, opacity: 0.45 }
        }
        transition={{
          duration: status === "listening" ? 1.4 : 5,
          repeat: Infinity,
          ease: "easeInOut",
        }}
      />
      <div className="orb-orbit orbit-one" />
      <div className="orb-orbit orbit-two" />
      <motion.div
        className="orb-core"
        animate={animate ? { scale: [1, 1.018, 1] } : { scale: 1 }}
        transition={{ duration: 3.6, repeat: Infinity }}
      >
        <div className="orb-shine" />
        {status === "completed" ? (
          <Check />
        ) : status === "emergency_stopped" || status === "failed" ? (
          <ShieldAlert />
        ) : status === "offline" ? (
          <Power />
        ) : (
          <AudioLines />
        )}
      </motion.div>
      <span className="orb-point point-one" />
      <span className="orb-point point-two" />
      <span className="orb-cross cross-one">+</span>
      <span className="orb-cross cross-two">+</span>
    </div>
  );
}
