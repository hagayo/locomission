-- Three independently sourced Hebrew mission *candidates* for editorial review.
-- Intentionally status='draft': the system must never invent a human reviewer.
-- Coordinates are APPROXIMATE editorial placeholders, NOT surveyed POI positions.
-- Verify every point, accessible public viewing location, safety and visual criteria
-- before manual approval. Does not become available to players automatically.
BEGIN;
INSERT INTO missions
(slug,title_he,historical_fact_he,instructions_he,visual_criteria_he,
 source_title,source_url,review_note,status,location,radius_m,points)
VALUES
('tel-aviv-independence-hall',
 'הבית שהפך לרגע של מדינה',
 'בבית מספר 16 בשדרות רוטשילד נערך ב-14 במאי 1948 טקס הכרזת העצמאות. לפני כן שימש המבנה ביתו של מאיר דיזנגוף, ולאחר מכן חלק ממוזיאון תל אביב. מקום אחד מילא כמה תפקידים בתולדות העיר והמדינה.',
 'מצא את חזית בית העצמאות וצלם קומפוזיציה מקורית שמבליטה פרט אדריכלי והקשר לרחוב.',
 ARRAY['חזית המבנה ההיסטורי נראית','אפשר להבחין בפרט אדריכלי אחד לפחות','התצלום צולם מהמרחב הציבורי ולא מתוך אזור סגור'],
 'עיריית תל אביב-יפו - שביל העצמאות',
 'https://www.tel-aviv.gov.il/en/Pages/IndependenceTrailen.aspx',
 'מקור נבדק; דרושים אישור עורך אנושי, אימות מיקום מדויק ונגישות צילום.', 'draft',
 ST_SetSRID(ST_MakePoint(34.769,32.063),4326)::geography,250,30),
('jaffa-clock-tower',
 'מי היה צריך שעון במרכז יפו?',
 'מגדל השעון ביפו הוקם בתקופה העותמאנית, בתחילת המאה ה-20, והוא מסמן את המפגש בין העיר העתיקה למרחב העירוני שסביבה. כיום השעון ממשיך לשמש נקודת ציון מרכזית.',
 'צלם את המגדל כך שאפשר יהיה לזהות בבירור לפחות אחד השעונים, לצד פרט סביבתי נוסף שמספר על הכיכר.',
 ARRAY['מגדל השעון נראה בפריים','לפחות לוח שעון אחד ניתן לזיהוי','נראה גם חלק מהכיכר או מסביבת הרחוב'],
 'Visit Tel Aviv - The Old City of Jaffa',
 'https://www.visit-tel-aviv.com/en/The-Old-City-of-Jaffa',
 'למקורות שונים שנות הקמה שונות; נמנענו מציון שנה מדויקת. מיקום משוער, מחייב בדיקה ואישור אנושי.', 'draft',
 ST_SetSRID(ST_MakePoint(34.752,32.055),4326)::geography,250,25),
('sarona-history',
 'כפר חקלאי בלב העיר',
 'לפני שמגדלי תל אביב הקיפו את האזור, שרונה הייתה מושבה חקלאית שהקימו טמפלרים גרמנים בשנת 1871. בתי האבן שנותרו מזכירים כיצד השתנה המקום משולי העיר למרכז עירוני.',
 'בחר מבנה משומר וצלם אותו יחד עם סימן ברור לעיר המודרנית שסביבו, כדי להמחיש את שינוי הזמן.',
 ARRAY['נראה מבנה היסטורי משומר במתחם שרונה','נראה ברקע פרט עירוני מודרני','התמונה מראה ניגוד ברור בין הישן לחדש'],
 'רשות העתיקות - תל אביב, הקריה',
 'https://hadashot.iaa.org.il/report_detail_eng.aspx?id=8512',
 'מקור נבדק; לאימות אנושי: נקודת המיקום, אילו מבנים פתוחים לצילום, ובטיחות.', 'draft',
 ST_SetSRID(ST_MakePoint(34.787,32.072),4326)::geography,300,25)
ON CONFLICT (slug) DO NOTHING;
COMMIT;
