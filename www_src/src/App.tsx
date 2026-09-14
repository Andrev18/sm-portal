import React, { useState } from 'react';
import Sidebar from './components/Sidebar';
import Header from './components/Header';
import FlashcardDashboard from './components/FlashcardDashboard';
import HermesCompanion from './components/HermesCompanion';

function App() {
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  return (
    <div className="flex h-screen w-screen bg-background overflow-hidden text-white font-sans selection:bg-primary/30">
      
      {/* Background ambient effects - zredukowane by poprawić czytelność i czystość */}
      <div className="absolute inset-0 pointer-events-none z-0 overflow-hidden flex justify-center opacity-50">
        <div className="absolute top-[-20%] left-[-10%] w-[50%] h-[50%] rounded-full bg-primary/10 blur-[150px]"></div>
        <div className="absolute top-[20%] right-[-10%] w-[40%] h-[60%] rounded-full bg-secondary/5 blur-[150px]"></div>
      </div>

      {/* Panele nawigacyjne */}
      <Sidebar isOpen={isSidebarOpen} setIsOpen={setIsSidebarOpen} />

      <div className="flex-1 flex flex-col h-full relative z-10 min-w-0 transition-all duration-300 bg-[#121212]/30">
        
        {/* Odciążony Header z głównymi zakładkami */}
        <Header toggleSidebar={() => setIsSidebarOpen(!isSidebarOpen)} isSidebarOpen={isSidebarOpen} />
        
        {/* Główny widok: Szybki panel powtórek fiszek */}
        <main className="flex-1 overflow-y-auto custom-scrollbar relative px-4 py-8 md:px-8">
          <div className="w-full max-w-6xl mx-auto">
            <FlashcardDashboard />
          </div>
        </main>

      </div>

      {/* Asystent chat */}
      <HermesCompanion />

    </div>
  );
}

export default App;
