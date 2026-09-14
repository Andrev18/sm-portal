import React from 'react';
import { motion } from 'framer-motion';
import { cn } from '../utils/cn';

interface ProgressLineProps {
  percentage: number;
  colorClass?: string;
}

export const ProgressLine: React.FC<ProgressLineProps> = ({ percentage, colorClass }) => {
  return (
    <div className="h-1.5 w-full bg-black/40 rounded-full overflow-hidden border border-white/5">
      <motion.div
        initial={{ width: 0 }}
        whileInView={{ width: `${percentage}%` }}
        viewport={{ once: true }}
        transition={{ duration: 1.2, ease: [0.16, 1, 0.3, 1] }}
        className={cn("h-full rounded-full relative", colorClass || "bg-primary")}
      >
        <div className="absolute inset-x-0 bottom-0 top-0 bg-gradient-to-r from-transparent to-white/30"></div>
      </motion.div>
    </div>
  );
};
