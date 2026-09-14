import React from 'react';
import { motion } from 'framer-motion';
import { Sparkles, ArrowRight } from 'lucide-react';

const HeroCommand = () => {
  const currentHour = new Date().getHours();
  const greeting = currentHour < 12 ? 'Dzień dobry' : currentHour < 18 ? 'Popołudnie' : 'Dobry wieczór';

  return (
    <section className="relative w-full max-w-7xl mx-auto px-6 py-12 lg:py-20 mt-4 mb-4 z-10">
      
      {/* Tło hero: dekoracyjne elementy prosto ze świata Apple/Stripe */}
      <div className="absolute top-0 right-0 w-3/4 h-full pointer-events-none opacity-40">
        <div className="absolute top-[-20%] right-[-10%] w-96 h-96 bg-primary/20 blur-[130px] rounded-full"></div>
        <div className="absolute bottom-[10%] right-[20%] w-80 h-80 bg-accent/20 blur-[100px] rounded-full mix-blend-screen"></div>
      </div>

      <motion.div 
        initial={{ opacity: 0, y: 30 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.8, ease: [0.16, 1, 0.3, 1] }}
        className="relative z-10 max-w-2xl"
      >
        <div className="inline-flex items-center gap-2 px-3 py-1 rounded-full bg-primary/10 border border-primary/20 text-primary text-xs font-mono mb-6">
          <Sparkles className="w-3.5 h-3.5" />
          <span>Synchronizacja zakończona sukcesem</span>
        </div>
        
        <h1 className="text-5xl lg:text-6xl font-bold tracking-tight mb-6">
          {greeting}, <br/>
          <span className="text-transparent bg-clip-text bg-gradient-to-r from-primary via-accent to-secondary animate-shimmer bg-[length:200%_auto]">
            Gotowy by przesuwać granice?
          </span>
        </h1>
        
        <p className="text-lg text-textSecondary mb-10 max-w-xl leading-relaxed">
          System Ośrodka wygenerował nowe połączenia synaptyczne w Twoim materiale szkoleniowym. Masz <b>142</b> rekomendowane operacje powtórzeniowe by zoptymalizować retencję przed sesją wieczorną.
        </p>
        
        <div className="flex items-center gap-4">
          <button className="group flex items-center gap-2 px-6 py-3 rounded-xl bg-white text-black font-semibold hover:bg-white/90 transition-all hover:scale-105 active:scale-95 shadow-[0_0_30px_rgba(255,255,255,0.1)]">
            <span>Diagnostyka Sesji</span>
            <ArrowRight className="w-4 h-4 group-hover:translate-x-1 transition-transform" />
          </button>
          
          <button className="px-6 py-3 rounded-xl bg-white/5 border border-white/10 text-white font-medium hover:bg-white/10 transition-colors backdrop-blur-sm">
            Przeglądaj Archiwum
          </button>
        </div>
      </motion.div>
    </section>
  );
};

export default HeroCommand;
