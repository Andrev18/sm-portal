import React from 'react';
import { motion } from 'framer-motion';
import { Play, Flame, Target, Trophy, Clock, Zap } from 'lucide-react';
import { mockSubjects, globalStats } from '../data/mockData';
import { cn } from '../utils/cn';
import { ProgressLine } from './SubjectStats';

const FlashcardDashboard = () => {
  const currentHour = new Date().getHours();
  const greeting = currentHour < 12 ? 'Dzień dobry' : currentHour < 18 ? 'Cześć' : 'Dobry wieczór';

  return (
    <div className="w-full flex flex-col gap-10">
      
      {/* 1. SEKCJA WITALNA - Proste przywitanie i gamifikacja zamiast ciężkiego ekranu "sci-fi" */}
      <div className="flex flex-col sm:flex-row justify-between items-start sm:items-end gap-6 pt-4">
        <motion.div
           initial={{ opacity: 0, x: -20 }}
           animate={{ opacity: 1, x: 0 }}
        >
          <h1 className="text-3xl md:text-4xl font-bold tracking-tight mb-2">
            {greeting} Kamil! 👋
          </h1>
          <p className="text-textSecondary text-lg max-w-xl">
            Czwartek pod znakiem nowej wiedzy. Pamiętaj - regularność to klucz.
          </p>
        </motion.div>

        {/* Streak & Level - Ważne dla angażowania 12-latków */}
        <motion.div 
          initial={{ opacity: 0, scale: 0.9 }}
          animate={{ opacity: 1, scale: 1 }}
          className="flex items-center gap-3 bg-surface/50 p-2 rounded-2xl border border-white/5"
        >
          <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-orange-500/20 to-red-500/20 rounded-xl border border-orange-500/20 text-orange-400">
            <Flame className="w-5 h-5 fill-current" />
            <span className="font-bold">12 dni z rzędu</span>
          </div>
          <div className="flex items-center gap-2 px-4 py-2 bg-gradient-to-r from-primary/20 to-primary/10 rounded-xl border border-primary/20 text-primary">
             <Trophy className="w-5 h-5" />
             <span className="font-bold">Poziom 14</span>
          </div>
        </motion.div>
      </div>

      {/* 2. GŁÓWNA AKCJA - Serce systemu Fiszek */}
      <motion.div 
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        className="w-full flex flex-col md:flex-row gap-6"
      >
        {/* Karta Startu Nauki (Duża, dominująca, przejrzysta) */}
        <div className="flex-1 relative overflow-hidden rounded-3xl border border-primary/30 p-8 flex flex-col md:flex-row items-center justify-between gap-8 group">
          {/* Tło karty */}
          <div className="absolute inset-0 bg-gradient-to-br from-primary/10 via-surface to-surface z-0"></div>
          <div className="absolute top-0 right-0 w-64 h-64 bg-primary/20 rounded-full blur-[100px] opacity-50 group-hover:opacity-100 transition-opacity z-0"></div>
          
          <div className="relative z-10 flex-1 w-full text-center md:text-left">
             <div className="inline-flex items-center gap-2 px-3 py-1 bg-white/5 rounded-lg text-primary text-sm font-medium mb-4">
                <Target className="w-4 h-4" /> Dzienny Cel: Dostępny
             </div>
             <h2 className="text-3xl md:text-4xl font-bold mb-3 text-white">Czas na powtórkę</h2>
             <p className="text-textSecondary text-base">
               Masz do przejrzenia <strong className="text-white text-lg">142</strong> nowe fiszki.<br/>
               Zajmie Ci to około <strong>15 minut</strong>.
             </p>
          </div>

          <div className="relative z-10 w-full md:w-auto">
             {/* Główny przycisk uruchamiający naukę */}
             <button className="w-full md:w-auto flex items-center justify-center gap-3 px-10 py-5 bg-white text-black hover:bg-primary hover:text-white rounded-2xl font-bold text-lg shadow-[0_0_20px_rgba(255,255,255,0.1)] hover:shadow-[0_0_40px_rgba(158,127,255,0.4)] transition-all duration-300 hover:scale-105 active:scale-95 group/btn">
               Rozpocznij naukę
               <Play className="w-6 h-6 fill-current group-hover/btn:translate-x-1 transition-transform" />
             </button>
          </div>
        </div>

        {/* Karta Skrótu Dziennika Vulcan (Bo nastolatków obchodzą oceny i spr.) */}
        <div className="md:w-[320px] rounded-3xl border border-white/5 bg-surface/50 p-6 flex flex-col hover:bg-surface/80 transition-colors">
          <div className="flex items-center justify-between mb-4">
             <h3 className="text-lg font-medium text-white flex items-center gap-2">
               <Zap className="w-5 h-5 text-secondary" /> Najbliższe Sprawdziany
             </h3>
          </div>
          <div className="space-y-3 flex-1">
             <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                <div className="flex justify-between items-start mb-1">
                   <span className="font-semibold text-white text-sm">Historia - Średniowiecze</span>
                   <span className="text-xs px-2 py-0.5 rounded-full bg-error/20 text-error">Jutro</span>
                </div>
                <div className="text-xs text-textSecondary flex items-center gap-1">
                   <Clock className="w-3 h-3" /> Fiszki pokrywają 85% tematu
                </div>
             </div>
             <div className="bg-white/5 p-3 rounded-xl border border-white/5">
                <div className="flex justify-between items-start mb-1">
                   <span className="font-semibold text-white text-sm">Fizyka - Kinematyka</span>
                   <span className="text-xs px-2 py-0.5 rounded-full bg-warning/20 text-warning">Za 3 dni</span>
                </div>
             </div>
          </div>
        </div>
      </motion.div>

      {/* 3. TWOJE PRZEDMIOTY (Prosty podział materiału bez technicznego żargonu) */}
      <div className="mt-4">
        <div className="flex items-center justify-between mb-6">
          <h2 className="text-2xl font-bold text-white">Twoje przedmioty i decki</h2>
          <button className="text-sm font-medium text-primary bg-primary/10 hover:bg-primary/20 px-4 py-2 rounded-xl transition-colors">
             + Dodaj talię
          </button>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-5">
          {mockSubjects.map((subject, index) => (
            <motion.div
              key={subject.id}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.3, delay: index * 0.08 }}
              className="group relative cursor-pointer overflow-hidden rounded-3xl border border-white/5 bg-surface/40 hover:bg-surface highlight-border transition-all duration-300"
            >
              {/* Przyciągające wzrok, ale delikatne kolory dla dzieciarni */}
              <div className={cn(
                "absolute -top-10 -right-10 w-32 h-32 blur-3xl opacity-20 group-hover:opacity-40 transition-opacity rounded-full",
                subject.color
              )} />

              <div className="p-5">
                <div className="flex items-center gap-4 mb-5">
                  <div className={cn("p-3 rounded-2xl bg-white/5 border border-white/5 shadow-sm", subject.iconColor)}>
                    <subject.icon className="w-6 h-6" />
                  </div>
                  <div>
                    <h3 className="font-bold text-white text-lg leading-tight group-hover:text-primary transition-colors">{subject.name}</h3>
                  </div>
                </div>

                <div className="flex items-center justify-between bg-black/20 rounded-xl p-3 mb-4">
                   <div className="text-left">
                      <div className="text-[10px] uppercase text-textSecondary font-medium">Do powtórzenia</div>
                      <div className={cn(
                        "text-xl font-bold",
                        subject.stats.toReviewToday > 0 ? "text-white" : "text-textSecondary"
                      )}>
                        {subject.stats.toReviewToday} <span className="text-sm font-normal opacity-50">/{subject.stats.totalCards}</span>
                      </div>
                   </div>
                   
                   {/* Mini ikonka 'play' pokazująca się przy przedmiocie, zachęcająca do kliknięcia */}
                   <div className="w-8 h-8 rounded-full bg-white/5 flex items-center justify-center group-hover:bg-primary group-hover:text-white transition-all text-textSecondary">
                      <Play className="w-3.5 h-3.5 fill-current ml-0.5" />
                   </div>
                </div>

                <div className="pt-2">
                  <div className="flex justify-between text-xs font-medium mb-2">
                    <span className="text-textSecondary">Opanowano materiał</span>
                    <span className="text-white">{subject.stats.mastery}%</span>
                  </div>
                  <ProgressLine percentage={subject.stats.mastery} colorClass="bg-primary" />
                </div>
              </div>
            </motion.div>
          ))}
        </div>
      </div>

    </div>
  );
};

export default FlashcardDashboard;
