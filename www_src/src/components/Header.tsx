import React from 'react';
import { Search, Command, Bell, Settings, PanelLeft, PanelLeftClose } from 'lucide-react';
import { motion, AnimatePresence } from 'framer-motion';
import { cn } from '../utils/cn';

interface HeaderProps {
  toggleSidebar: () => void;
  isSidebarOpen: boolean;
}

const Header: React.FC<HeaderProps> = ({ toggleSidebar, isSidebarOpen }) => {
  const [activeMenu, setActiveMenu] = React.useState('Panel Fiszek');
  
  // Zoptymalizowane menu górne - zbiera nadmiar rzeczy z głównego pulpitu
  const topMenuItems = ['Panel Fiszek', 'Dziennik Vulcan', 'Wszystkie Aplikacje', 'Ranking', 'Zadania Domowe']; 

  return (
    <header className="h-16 flex-shrink-0 w-full px-4 md:px-6 flex items-center justify-between border-b border-white/5 bg-background/80 backdrop-blur-xl sticky top-0 z-30">
      
      <div className="flex items-center gap-6">
        <button 
          onClick={toggleSidebar}
          className="p-2 rounded-lg text-textSecondary hover:bg-white/10 hover:text-white transition-all outline-none"
        >
          {isSidebarOpen ? <PanelLeftClose className="w-5 h-5" /> : <PanelLeft className="w-5 h-5" />}
        </button>

        {/* Menu przeniesione z kokpitu - odciąża widok główny */}
        <nav className="hidden lg:flex items-center gap-1 bg-white/[0.03] p-1 rounded-xl border border-white/5 shadow-sm">
          {topMenuItems.map((item) => (
            <button
              key={item}
              onClick={() => setActiveMenu(item)}
              className={cn(
                "relative px-4 py-1.5 rounded-lg text-sm font-medium transition-all duration-300",
                activeMenu === item ? "text-white" : "text-textSecondary hover:text-white hover:bg-white/5"
              )}
            >
              {activeMenu === item && (
                <motion.div 
                  layoutId="header-active-bg"
                  className="absolute inset-0 bg-white/10 rounded-lg -z-10 shadow-[0_0_10px_rgba(255,255,255,0.05)]"
                  transition={{ type: "spring", stiffness: 400, damping: 30 }}
                />
              )}
              {item}
            </button>
          ))}
        </nav>
      </div>

      {/* Szukanie i powiadomienia */}
      <div className="flex items-center gap-2 md:gap-4">
        <motion.button 
          whileHover={{ scale: 1.02 }}
          whileTap={{ scale: 0.98 }}
          className="hidden sm:flex items-center gap-3 px-3 py-1.5 rounded-lg bg-black/40 border border-white/10 text-sm text-textSecondary hover:bg-white/5 transition-colors focus:ring-1 focus:ring-primary/50"
        >
          <Search className="w-4 h-4 opacity-70" />
          <span className="font-sans pr-6">Szukaj...</span>
          <div className="flex items-center gap-1 opacity-40">
            <Command className="w-3 h-3" />
            <span className="text-xs uppercase font-mono">K</span>
          </div>
        </motion.button>

        <div className="hidden sm:block h-6 w-px bg-white/10 mx-1"></div>

        <button className="relative p-2 text-textSecondary hover:text-white transition-colors rounded-lg hover:bg-primary/10 hover:text-primary group">
          <Bell className="w-5 h-5 group-hover:scale-110 transition-transform" />
          <span className="absolute top-1.5 right-1.5 w-2 h-2 rounded-full bg-accent animate-pulse shadow-[0_0_10px_rgba(244,114,182,0.8)]"></span>
        </button>

        <button className="p-2 text-textSecondary hover:text-white transition-colors rounded-lg hover:bg-white/5">
          <Settings className="w-5 h-5" />
        </button>
      </div>
    </header>
  );
};

export default Header;
