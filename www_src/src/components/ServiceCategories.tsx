import React from 'react';
import { motion } from 'framer-motion';
import { smCategories } from '../data/mockData';
import { ExternalLink } from 'lucide-react';
import { cn } from '../utils/cn';

const ServiceCategories = () => {
  return (
    <div className="mt-12">
      <div className="flex items-center gap-3 mb-8">
        <h2 className="text-xl font-mono text-textSecondary">Ekosystem_Ośrodka</h2>
        <div className="hidden sm:block flex-1 w-32 h-[1px] bg-gradient-to-r from-white/10 to-transparent ml-4"></div>
      </div>

      <div className="space-y-10">
        {smCategories.map((group, groupIdx) => (
          <div key={group.groupId}>
            <div className="flex items-center gap-2 mb-4 pl-1">
              <group.icon className="w-5 h-5 text-textSecondary" />
              <h3 className="text-lg font-semibold text-white/90">{group.title}</h3>
            </div>
            
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4">
              {group.items.map((item, itemIdx) => (
                <motion.a
                  href={item.url}
                  key={item.id}
                  initial={{ opacity: 0, y: 10 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: (groupIdx * 0.1) + (itemIdx * 0.05) }}
                  className="group relative flex flex-col p-5 rounded-2xl bg-surface/40 border border-white/5 hover:bg-surface hover:border-white/10 transition-all duration-300 hover:-translate-y-1"
                >
                  <div className="flex justify-between items-start mb-3">
                    <h4 className="font-semibold text-white text-sm group-hover:text-primary transition-colors">
                      {item.name}
                    </h4>
                    <ExternalLink className="w-4 h-4 text-white/30 group-hover:text-primary transition-colors" />
                  </div>
                  <p className="text-xs text-textSecondary mt-auto">
                    {item.description}
                  </p>
                  
                  {/* Subtle hover gleam effect */}
                  <div className="absolute inset-0 rounded-2xl bg-gradient-to-tr from-white/0 via-white/0 to-white/5 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"></div>
                </motion.a>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default ServiceCategories;
