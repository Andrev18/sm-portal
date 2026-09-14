import { 
  Calculator, FlaskConical, Book, TerminalSquare, Compass, 
  Library, MonitorPlay, Wrench, Globe, FileKey, GraduationCap
} from 'lucide-react';

// Globalne statystyki
export const globalStats = {
  totalReviewsToday: 142,
  cardsMastered: 843,
  retentionRate: 94.2,
  activeStreak: 12
};

// Moduł 1: Repozytoria Przedmiotów
export const mockSubjects = [
  { id: 'math', name: 'Analiza Matematyczna', code: 'MATH-101', icon: Calculator, color: 'from-secondary to-blue-600', iconColor: 'text-secondary', stats: { toReviewToday: 42, totalCards: 320, mastery: 78 } },
  { id: 'physics', name: 'Fizyka Elementarna', code: 'PHYS-202', icon: FlaskConical, color: 'from-success to-emerald-600', iconColor: 'text-success', stats: { toReviewToday: 15, totalCards: 210, mastery: 92 } },
  { id: 'literature', name: 'Teoria Literatury', code: 'LIT-301', icon: Book, color: 'from-accent to-rose-600', iconColor: 'text-accent', stats: { toReviewToday: 0, totalCards: 158, mastery: 100 } },
  { id: 'programming', name: 'Podstawy Inżynierii', code: 'CS-101', icon: TerminalSquare, color: 'from-primary to-purple-600', iconColor: 'text-primary', stats: { toReviewToday: 85, totalCards: 450, mastery: 65 } },
];

// Moduł 2: Minimalistyczna Integracja Vulcan (E-dziennik)
export const mockVulcanData = {
  student: "uczen_01",
  luckyNumber: 14,
  average: "4.78",
  presence: "96%",
  upcomingLesson: {
    time: "08:15",
    subject: "Matematyka (Rozsz)",
    room: "Sala 204",
    status: "in_30m"
  },
  recentGrades: [
    { subject: "Fizyka", value: "5", weight: 3, date: "Dzisiaj" },
    { subject: "J. Polski", value: "4+", weight: 2, date: "Wczoraj" },
    { subject: "Informatyka", value: "6", weight: 4, date: "2 dni temu" }
  ]
};

// Moduł 3: Serie Kafelków (sm.b2bgliwice wzór)
export const smCategories = [
  {
    groupId: "podreczniki",
    title: "Podręczniki",
    icon: Library,
    items: [
      { id: "p1", name: "Helion Edukacja", description: "Baza IT", url: "https://helion.pl" },
      { id: "p2", name: "WSiPnet", description: "Książki E-learning", url: "#" },
      { id: "p3", name: "Nowa Era (E-book)", description: "Platforma ucznia", url: "#" }
    ]
  },
  {
    groupId: "narzedzia",
    title: "Narzędzia",
    icon: Wrench,
    items: [
      { id: "n1", name: "Microsoft 365", description: "Pakiet Office", url: "#" },
      { id: "n2", name: "Dysk Google", description: "Przestrzeń 1TB", url: "#" },
      { id: "n3", name: "Kalkulator Ocen", description: "Symulator średniej", url: "#" },
      { id: "n4", name: "Generator Fisz", description: "Zasilanie AI", url: "#" }
    ]
  },
  {
    groupId: "serwisy",
    title: "Serwisy",
    icon: Globe,
    items: [
      { id: "s1", name: "System Vulcan", description: "E-dziennik UONET+", url: "#" },
      { id: "s2", name: "Platforma Moodle", description: "E-learning Ośrodka", url: "#" },
      { id: "s3", name: "Plan Lekcji Optivum", description: "Aktualny harmonogram", url: "#" },
      { id: "s4", name: "Biblioteka MOL", description: "Katalog książek", url: "#" }
    ]
  }
];
