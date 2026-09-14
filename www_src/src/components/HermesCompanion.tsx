import React, { useState } from 'react';
import { TerminalSquare, X, ChevronRight, Sparkles } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

const HermesCompanion = () => {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <>
      {/* Floating CLI Toggle Button */}
      <button
        onClick={() => setIsOpen(true)}
        className={`fixed bottom-6 right-6 z-50 p-4 rounded-2xl glass-panel-hover bg-background/80 border border-white/10 shadow-2xl flex items-center justify-center gap-3 group transition-transform ${isOpen ? 'scale-0 opacity-0' : 'scale-100 opacity-100'}`}
      >
        <TerminalSquare className="w-6 h-6 text-primary group-hover:text-glow" />
        <div className="flex flex-col items-start pr-2">
          <span className="text-xs font-mono font-semibold text-white">HERMES_AI</span>
          <span className="text-[10px] font-mono text-success flex items-center gap-1">
            <span className="w-1.5 h-1.5 rounded-full bg-success animate-pulse"></span>
            online
          </span>
        </div>
      </button>

      {/* Expanded CLI Window */}
      <AnimatePresence>
        {isOpen && (
          <motion.div
            initial={{ opacity: 0, y: 50, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 50, scale: 0.9 }}
            transition={{ type: "spring", bounce: 0.3, duration: 0.5 }}
            className="fixed bottom-6 right-6 z-50 w-80 md:w-96 rounded-2xl bg-black border border-white/10 shadow-2xl overflow-hidden flex flex-col"
          >
            {/* Terminal Top Bar */}
            <div className="bg-surface px-4 py-3 border-b border-white/10 flex items-center justify-between cursor-move select-none">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-primary" />
                <span className="font-mono text-xs text-textSecondary">root@hermes:~</span>
              </div>
              <button 
                onClick={() => setIsOpen(false)}
                className="text-white/40 hover:text-white transition-colors"
              >
                <X className="w-4 h-4" />
              </button>
            </div>

            {/* Terminal Body */}
            <div className="p-4 h-64 overflow-y-auto font-mono text-xs flex flex-col gap-3">
              <div className="text-white/70">
                Inicjalizacja modułu asystenta... [OK]<br/>
                Odzyskiwanie kontekstu nauki... [OK]
              </div>
              
              <div className="flex items-start gap-2 text-primary">
                <ChevronRight className="w-4 h-4 shrink-0 mt-0.5" />
                <p>
                  Witaj w systemie. Analiza wykazała, że masz powtórki do zrobienia z działu: <span className="text-white bg-white/10 px-1 rounded">Trygonometria</span>. Czy rozpocząć wczytywanie fiszek?
                </p>
              </div>

              <div className="mt-auto pt-4 flex items-center gap-2 text-white/50">
                <ChevronRight className="w-4 h-4" />
                <span className="animate-pulse">_</span>
              </div>
            </div>

            {/* Input Area */}
            <div className="bg-surface/50 border-t border-white/10 p-3">
              <input 
                type="text" 
                placeholder="Wpisz komendę lub zadaj pytanie..."
                className="w-full bg-transparent border-none text-white text-xs font-mono focus:outline-none placeholder:text-white/30"
              />
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </>
  );
};

export default HermesCompanion;
