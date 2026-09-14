import React, { useState } from 'react';
import { 
  Home, FolderGit2, Clock, 
  BookOpen, FileText, Activity, ChevronDown, 
  LogOut, Library, Hexagon
} from 'lucide-react';
import { cn } from '../utils/cn';
import { motion, AnimatePresence } from 'framer-motion';

interface SidebarProps {
  isOpen: boolean;
  setIsOpen: (val: boolean) => void;
}

// Drzewo kategorii rozwijanych w menu
const primaryMenu = [
  { id: 'osrodek', name: 'Ośrodek', icon: Home, isInteractive: true },
  { id: 'ostatnie', name: 'Ostatnie sesje', icon: Clock, isInteractive: true },
  { 
    id: 'przedmioty', 
    name: 'Przedmioty', 
    icon: FolderGit2,
    subItems: [
      { id: 'math', name: 'Analiza Matematyczna' },
      { id: 'phys', name: 'Fizyka Elementarna' },
      { id: 'lit', name: 'Teoria Literatury' },
      { id: 'cs', name: 'Systemy IT' },
    ]
  },
  { 
    id: 'podreczniki', 
    name: 'Podręczniki przedmiotami', 
    icon: Library,
    subItems: [
      { id: 'b_math', name: 'Matematyka (Rozsz.)' },
      { id: 'b_phys', name: 'Zbiór Zadań - Fizyka' },
      { id: 'b_lit', name: 'J. Polski - Epoce' },
      { id: 'b_cs', name: 'Informatyka: Helion' },
    ]
  },
];

const secondaryMenu = [
  { id: 'pomoc', name: 'Centrum pomocy', icon: BookOpen },
  { id: 'akt', name: 'Aktualizacje', icon: FileText },
  { id: 'status', name: 'Status', icon: Activity },
];

const Sidebar: React.FC<SidebarProps> = ({ isOpen, setIsOpen }) => {
  const [activeItem, setActiveItem] = useState('osrodek');
  const [expandedBlocks, setExpandedBlocks] = useState<string[]>(['przedmioty']);

  const toggleBlock = (id: string) => {
    if (!isOpen) setIsOpen(true); // Automatycznie poszerz sidebar gdy kliknięto na zwiniętym
    setExpandedBlocks(prev => 
      prev.includes(id) ? prev.filter(b => b !== id) : [...prev, id]
    );
  };

  const handleItemClick = (id: string, hasSubItems: boolean) => {
    if (hasSubItems) {
      toggleBlock(id);
    } else {
      setActiveItem(id);
      if (!isOpen) setIsOpen(true);
    }
  };

  return (
    <motion.aside 
      initial={false}
      animate={{ width: isOpen ? 260 : 80 }}
      transition={{ duration: 0.3, ease: [0.16, 1, 0.3, 1] }}
      className="h-full flex flex-col bg-surface border-r border-white/5 flex-shrink-0 relative z-20 overflow-hidden"
    >
      {/* Oznaczenie Wersji Systemu & Logo Ośrodka (góra sidebaru) */}
      <div className={cn(
        "p-4 flex items-center transition-all duration-300", 
        !isOpen ? "justify-center" : "gap-3"
      )}>
         <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-primary to-accent p-[1px] flex-shrink-0 shadow-[0_0_20px_rgba(158,127,255,0.2)]">
            <div className="w-full h-full bg-surface rounded-[11px] flex items-center justify-center">
               <Hexagon className="w-6 h-6 text-primary fill-primary/10" />
            </div>
         </div>
         <AnimatePresence>
            {isOpen && (
              <motion.div 
                initial={{ opacity: 0, width: 0 }}
                animate={{ opacity: 1, width: 'auto' }}
                exit={{ opacity: 0, width: 0 }}
                className="flex flex-col whitespace-nowrap overflow-hidden"
              >
                <span className="font-bold text-white tracking-wide">OŚRODEK</span>
                <span className="text-[10px] text-textSecondary uppercase tracking-widest">v2.1.0 • Core</span>
              </motion.div>
            )}
         </AnimatePresence>
      </div>

      <div className="flex-1 overflow-y-auto w-full custom-scrollbar py-2 flex flex-col gap-6">
        
        {/* Nawigacja Główna z Rozwijanymi Listami */}
        <nav className="px-3 space-y-1">
          {primaryMenu.map((item) => {
            const hasSub = !!item.subItems;
            const isExpanded = expandedBlocks.includes(item.id);
            const isActive = activeItem === item.id;

            return (
              <div key={item.id} className="w-full">
                <button
                  onClick={() => handleItemClick(item.id, hasSub)}
                  title={!isOpen ? item.name : undefined}
                  className={cn(
                    "w-full flex items-center rounded-lg text-sm transition-all duration-200 outline-none group",
                    isOpen ? "px-3 py-2.5 gap-3" : "py-3 justify-center mb-1",
                    isActive && !hasSub
                      ? "bg-white/10 text-white font-medium shadow-[0_0_15px_rgba(255,255,255,0.05)]" 
                      : "text-textSecondary hover:bg-white/5 hover:text-white"
                  )}
                >
                  <item.icon className={cn(
                    "w-[20px] h-[20px] flex-shrink-0 transition-all duration-300", 
                    isActive && !hasSub ? "text-primary scale-110" : "opacity-70 group-hover:scale-110 group-hover:text-white"
                  )} />
                  
                  <AnimatePresence>
                    {isOpen && (
                      <motion.span
                        initial={{ opacity: 0, x: -10 }}
                        animate={{ opacity: 1, x: 0 }}
                        exit={{ opacity: 0, width: 0, md: 0 }}
                        className="whitespace-nowrap overflow-hidden text-left flex-1"
                      >
                        {item.name}
                      </motion.span>
                    )}
                  </AnimatePresence>

                  {/* Ikona Rozwijania dla kategorii ze strukturą */}
                  {isOpen && hasSub && (
                    <ChevronDown 
                      className={cn(
                        "w-4 h-4 text-textSecondary opacity-50 transition-transform duration-300",
                        isExpanded ? "rotate-180" : ""
                      )}
                    />
                  )}
                </button>

                {/* Sekcja Dzieci Modyfikowalna - Akordeon */}
                <AnimatePresence>
                  {isOpen && hasSub && isExpanded && (
                    <motion.div
                      initial={{ height: 0, opacity: 0 }}
                      animate={{ height: 'auto', opacity: 1 }}
                      exit={{ height: 0, opacity: 0 }}
                      transition={{ duration: 0.2, ease: "easeOut" }}
                      className="overflow-hidden"
                    >
                      <div className="pl-11 pr-2 py-1 flex flex-col gap-0.5">
                        {item.subItems.map((sub) => (
                           <button
                            key={sub.id}
                            className="w-full text-left py-1.5 px-3 rounded-md text-xs font-medium text-textSecondary hover:text-white hover:bg-white/5 transition-colors truncate"
                           >
                              {sub.name}
                           </button>
                        ))}
                      </div>
                    </motion.div>
                  )}
                </AnimatePresence>
              </div>
            );
          })}
        </nav>

        {/* Separator */}
        <div className="px-6">
          <div className="h-[1px] w-full bg-white/5"></div>
        </div>

        {/* Sekcja Pomocnicza */}
        <nav className="px-3 space-y-1">
          {secondaryMenu.map((item) => (
             <button
              key={item.id}
              title={!isOpen ? item.name : undefined}
              className={cn(
                "w-full flex items-center rounded-lg text-sm text-textSecondary hover:bg-white/5 hover:text-white transition-all duration-200 outline-none group",
                isOpen ? "px-3 py-2.5 gap-3" : "py-3 justify-center"
              )}
            >
              <item.icon className="w-[18px] h-[18px] opacity-70 flex-shrink-0 group-hover:text-white" />
              <AnimatePresence>
                {isOpen && (
                  <motion.span
                    initial={{ opacity: 0 }}
                    animate={{ opacity: 1 }}
                    exit={{ opacity: 0, width: 0 }}
                    className="whitespace-nowrap overflow-hidden text-left flex-1"
                  >
                    {item.name}
                  </motion.span>
                )}
              </AnimatePresence>
            </button>
          ))}
        </nav>
      </div>

      {/* Profil Użytkownika & Wylogowanie na dole */}
      <div className="p-4 mt-auto border-t border-white/5 flex flex-col gap-2">
        <button className={cn(
          "flex items-center p-2 rounded-xl bg-white/[0.02] hover:bg-white/[0.06] transition-colors outline-none",
          isOpen ? "w-full justify-between" : "w-10 h-10 justify-center mx-auto"
        )}>
          <div className="flex items-center gap-3">
            <div className="w-7 h-7 rounded-lg bg-gradient-to-br from-primary to-secondary p-[1px] flex-shrink-0">
               <div className="w-full h-full bg-surface rounded-[7px] overflow-hidden">
                  <img 
                    src="https://images.pexels.com/photos/1762851/pexels-photo-1762851.jpeg?auto=compress&cs=tinysrgb&w=150" 
                    alt="User" 
                    className="w-full h-full object-cover opacity-90 grayscale hover:grayscale-0 transition-all"
                  />
               </div>
            </div>
            
            <AnimatePresence>
              {isOpen && (
                <motion.div 
                  initial={{ opacity: 0, width: 0 }}
                  animate={{ opacity: 1, width: 'auto' }}
                  exit={{ opacity: 0, width: 0 }}
                  className="flex flex-col text-left overflow-hidden whitespace-nowrap"
                >
                   <span className="text-sm font-medium text-white shadow-sm leading-tight">uczen_01</span>
                   <span className="text-[10px] text-textSecondary uppercase">Pro Plan</span>
                </motion.div>
              )}
            </AnimatePresence>
          </div>
        </button>

        <button 
          title={!isOpen ? "Wyloguj system" : undefined}
          className={cn(
            "w-full flex items-center rounded-lg text-sm text-textSecondary hover:bg-error/10 hover:text-error transition-all duration-200 outline-none",
            isOpen ? "gap-3 px-3 py-2" : "py-3 justify-center"
          )}
        >
          <LogOut className="w-[18px] h-[18px] opacity-70 flex-shrink-0" />
          <AnimatePresence>
            {isOpen && (
              <motion.span
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0, width: 0 }}
                className="whitespace-nowrap overflow-hidden"
              >
                Wyloguj system
              </motion.span>
            )}
          </AnimatePresence>
        </button>
      </div>
    </motion.aside>
  );
};

export default Sidebar;
