import React from 'react';
import { Home, Gamepad2, Award, BookOpen, Settings, X, Zap } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';

interface SidebarProps {
  isOpen: boolean;
  setIsOpen: (isOpen: boolean) => void;
}

const navItems = [
  { icon: Home, label: 'Baza Główna', id: 'home', active: true },
  { icon: Gamepad2, label: 'Moje Kolekcje', id: 'decks' },
  { icon: Award, label: 'Osiągnięcia i XP', id: 'stats' },
  { icon: BookOpen, label: 'E-Dziennik', id: 'vulcan' },
];

export default function Sidebar({ isOpen, setIsOpen }: SidebarProps) {
  return (
    <AnimatePresence mode="wait">
      {isOpen && (
        <motion.aside
          initial={{ x: -300, opacity: 0 }}
          animate={{ x: 0, opacity: 1 }}
          exit={{ x: -300, opacity: 0 }}
          transition={{ type: 'spring', stiffness: 300, damping: 30 }}
          className="fixed md:static inset-y-0 left-0 z-50 w-72 bg-surface/80 backdrop-blur-3xl border-r border-border flex flex-col justify-between"
        >
          <div>
            <div className="flex items-center justify-between p-6">
              <div className="flex items-center gap-3 text-primary font-bold text-xl tracking-tight">
                <div className="p-2 bg-primary/10 rounded-xl">
                  <Zap className="w-6 h-6 text-primary" />
                </div>
                <span>SuperFiszki</span>
              </div>
              <button 
                onClick={() => setIsOpen(false)}
                className="md:hidden p-2 text-textSecondary hover:text-text rounded-lg bg-background/50"
              >
                <X className="w-5 h-5" />
              </button>
            </div>

            <nav className="px-4 mt-4 space-y-2">
              {navItems.map((item) => (
                <button
                  key={item.id}
                  className={`w-full flex items-center gap-4 px-4 py-3.5 rounded-2xl transition-all duration-300 font-bold ${
                    item.active
                      ? 'bg-primary text-white shadow-lg shadow-primary/25'
                      : 'text-textSecondary hover:bg-white/5 hover:text-white'
                  }`}
                >
                  <item.icon className="w-5 h-5" />
                  {item.label}
                </button>
              ))}
            </nav>
          </div>

          <div className="p-4">
            <button className="w-full flex items-center gap-4 px-4 py-3.5 rounded-2xl text-textSecondary hover:bg-white/5 hover:text-white transition-all duration-300 font-bold">
              <Settings className="w-5 h-5" />
              Ustawienia
            </button>
          </div>
        </motion.aside>
      )}
    </AnimatePresence>
  );
}
