import React, { useState } from 'react';
import { MessageSquare, X, Send, Bot } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

export default function HermesCompanion() {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className="fixed bottom-6 right-6 z-50">
      <AnimatePresence>
        {isOpen && (
          <motion.div 
            initial={{ opacity: 0, y: 20, scale: 0.9 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 20, scale: 0.9 }}
            transition={{ type: "spring", stiffness: 300, damping: 25 }}
            className="absolute bottom-20 right-0 w-80 bg-surface/95 backdrop-blur-2xl border border-white/10 rounded-3xl shadow-2xl overflow-hidden"
          >
            <div className="p-4 border-b border-white/10 bg-black/20 flex items-center justify-between">
              <div className="flex items-center gap-3 text-white font-bold">
                <div className="w-8 h-8 rounded-full bg-primary/20 flex items-center justify-center text-primary">
                  <Bot className="w-5 h-5" />
                </div>
                Twój Kumpel AI
              </div>
              <button 
                onClick={() => setIsOpen(false)}
                className="text-textSecondary hover:text-white p-1 rounded-lg transition-colors"
              >
                <X className="w-5 h-5" />
              </button>
            </div>
            
            <div className="p-4 h-64 overflow-y-auto flex flex-col gap-3 text-sm">
              <div className="bg-primary/20 text-white rounded-2xl rounded-tl-sm p-3 w-[85%] border border-primary/30">
                Hej! 👋 Widzę, że przygotowujesz się do sprawdzianu z ułamków. Chcesz, żebym przypomniał Ci, jak działa mnożenie na krzyż?
              </div>
            </div>

            <div className="p-3 bg-black/20 border-t border-white/10 flex items-center gap-2">
              <input 
                type="text"
                placeholder="Napisz do mnie..." 
                className="flex-1 bg-surface text-white text-sm rounded-xl px-4 py-2 outline-none border border-white/10 focus:border-primary/50 transition-colors"
              />
              <button className="p-2 bg-primary text-white rounded-xl hover:bg-primary/90 shadow-lg shadow-primary/25">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      <motion.button
        whileHover={{ scale: 1.1 }}
        whileTap={{ scale: 0.9 }}
        onClick={() => setIsOpen(!isOpen)}
        className="w-14 h-14 bg-primary text-white rounded-full flex items-center justify-center shadow-[0_0_30px_-5px_rgba(158,127,255,0.6)] hover:shadow-[0_0_40px_-5px_rgba(158,127,255,0.8)] transition-all"
      >
        {isOpen ? <X className="w-6 h-6" /> : <MessageSquare className="w-6 h-6" />}
      </motion.button>
    </div>
  );
}
