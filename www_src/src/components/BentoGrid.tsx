import React from 'react';
import { motion } from 'framer-motion';
import { Sparkles, Book, Calculator, FlaskConical, Globe, Code } from 'lucide-react';
import { cn } from '../utils/cn';

const gridItems = [
  {
    title: 'Super Pamięć',
    description: 'Aplikacja podpowiada Ci dokładnie to, co zaraz zapomnisz. Magia!',
    icon: Sparkles,
    className: 'md:col-span-2 md:row-span-2',
    gradient: 'from-purple-500/20 to-indigo-500/20 hover:from-purple-500/30 hover:to-indigo-500/30',
    iconColor: 'text-purple-400',
    type: 'funkcja',
  },
  {
    title: 'Matematyka',
    description: 'Ułamki i geometria bez tajemnic.',
    icon: Calculator,
    className: 'md:col-span-1',
    gradient: 'from-secondary/20 to-blue-500/20 hover:from-secondary/30 hover:to-blue-500/30',
    iconColor: 'text-secondary',
    type: 'przedmiot',
  },
  {
    title: 'Fizyka na luzie',
    description: 'Grawitacja, prąd, magnesy.',
    icon: FlaskConical,
    className: 'md:col-span-1',
    gradient: 'from-emerald-500/20 to-teal-500/20 hover:from-emerald-500/30 hover:to-teal-500/30',
    iconColor: 'text-emerald-400',
    type: 'przedmiot',
  },
  {
    title: 'Język Polski',
    description: 'Lektury i szybkie ściągi.',
    icon: Book,
    className: 'md:col-span-1',
    gradient: 'from-accent/20 to-rose-500/20 hover:from-accent/30 hover:to-rose-500/30',
    iconColor: 'text-accent',
    type: 'przedmiot',
  },
  {
    title: 'Baza Wiedzy',
    description: 'Odkrywaj nowe przedmioty i talie kart stworzone przez innych.',
    icon: Globe,
    className: 'md:col-span-2',
    gradient: 'from-orange-500/20 to-amber-500/20 hover:from-orange-500/30 hover:to-amber-500/30',
    iconColor: 'text-orange-400',
    type: 'eksploruj',
  },
];

const BentoGrid = () => {
  return (
    <section className="max-w-7xl mx-auto px-6 pb-32">
      <div className="flex items-center gap-3 mb-8">
        <h2 className="text-2xl font-bold flex items-center gap-2">
          <span>Twoje Przedmioty</span>
        </h2>
        <div className="flex-1 h-[1px] bg-gradient-to-r from-white/10 to-transparent ml-4"></div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 auto-rows-[160px]">
        {gridItems.map((item, index) => (
          <motion.div
            key={index}
            initial={{ opacity: 0, scale: 0.95, y: 20 }}
            whileInView={{ opacity: 1, scale: 1, y: 0 }}
            viewport={{ once: true, margin: "-50px" }}
            transition={{ duration: 0.5, delay: index * 0.1 }}
            className={cn(
              "group relative overflow-hidden rounded-3xl border border-white/5 bg-surface/50 p-6 flex flex-col justify-between cursor-pointer transition-all duration-500 hover:-translate-y-1 hover:shadow-2xl hover:border-white/20",
              item.className
            )}
          >
            {/* Background Gradient */}
            <div className={cn(
              "absolute inset-0 bg-gradient-to-br opacity-50 transition-colors duration-500",
              item.gradient
            )} />
            
            {/* Top row: Icon and Type indicator */}
            <div className="relative z-10 flex justify-between items-start">
              <div className={cn("p-3 rounded-2xl bg-black/40 backdrop-blur-md border border-white/5", item.iconColor)}>
                <item.icon className="w-6 h-6" />
              </div>
              <span className="text-[10px] uppercase font-bold text-white/40 border border-white/10 px-2 py-1 rounded-full bg-black/20">
                {item.type}
              </span>
            </div>

            {/* Bottom row: Text data */}
            <div className="relative z-10 mt-auto">
              <h3 className="text-xl font-bold mb-2 group-hover:text-white transition-colors">
                {item.title}
              </h3>
              <p className="text-textSecondary text-sm line-clamp-2">
                {item.description}
              </p>
            </div>

            <div className="absolute inset-0 -translate-x-full bg-gradient-to-r from-transparent via-white/5 to-transparent group-hover:animate-[shimmer_1.5s_infinite]" />
          </motion.div>
        ))}
      </div>
    </section>
  );
};

export default BentoGrid;
