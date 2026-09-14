import React from 'react';
import { motion } from 'framer-motion';
import { GraduationCap, Clock, Award, Calculator, CalendarClock } from 'lucide-react';
import { mockVulcanData } from '../data/mockData';

const VulcanWidget = () => {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      className="col-span-1 lg:col-span-1 border border-white/10 bg-black/40 backdrop-blur-xl rounded-3xl p-6 relative overflow-hidden group hover:border-white/20 transition-all duration-300"
    >
      {/* Home Assistant / Minimalist Glow */}
      <div className="absolute top-[-50px] right-[-50px] w-32 h-32 bg-primary/20 rounded-full blur-[60px] pointer-events-none transition-all group-hover:bg-primary/30"></div>

      <div className="flex justify-between items-center mb-6 relative z-10">
        <div className="flex items-center gap-2 text-textSecondary font-mono text-sm">
          <GraduationCap className="w-4 h-4 text-primary" />
          <span>Integracja E-dziennik</span>
        </div>
        
        {/* Szczęśliwy Numerek */}
        <div className="flex items-center gap-2 bg-gradient-to-r from-accent/20 to-primary/20 px-3 py-1 rounded-full border border-white/5">
          <Award className="w-3.5 h-3.5 text-accent" />
          <span className="text-xs font-semibold text-white">Numerek: {mockVulcanData.luckyNumber}</span>
        </div>
      </div>

      <div className="space-y-6 relative z-10">
        {/* Główne Statystyki */}
        <div className="grid grid-cols-2 gap-4">
          <div className="bg-white/5 rounded-2xl p-4 border border-white/5">
            <div className="text-xs text-textSecondary mb-1 flex items-center gap-1">
              <Calculator className="w-3 h-3" /> Średnia
            </div>
            <div className="text-2xl font-bold font-mono text-white">
              {mockVulcanData.average}
            </div>
          </div>
          <div className="bg-white/5 rounded-2xl p-4 border border-white/5">
            <div className="text-xs text-textSecondary mb-1 flex items-center gap-1">
              <Clock className="w-3 h-3" /> Frekwencja
            </div>
            <div className="text-2xl font-bold font-mono text-success">
              {mockVulcanData.presence}
            </div>
          </div>
        </div>

        {/* Najbliższa lekcja (Home Assistant style card) */}
        <div className="bg-gradient-to-r from-primary/10 to-transparent p-4 rounded-2xl border border-primary/20 flex items-center justify-between">
          <div>
            <div className="text-xs text-primary font-mono mb-1 flex items-center gap-1">
              <CalendarClock className="w-3 h-3" /> Za 30 min
            </div>
            <div className="font-semibold text-white">{mockVulcanData.upcomingLesson.subject}</div>
            <div className="text-sm text-textSecondary">{mockVulcanData.upcomingLesson.room}</div>
          </div>
          <div className="text-right">
            <div className="text-xl font-mono text-white/90">{mockVulcanData.upcomingLesson.time}</div>
          </div>
        </div>
      </div>
    </motion.div>
  );
};

export default VulcanWidget;
