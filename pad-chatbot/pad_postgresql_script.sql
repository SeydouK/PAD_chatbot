CREATE USER psd WITH PASSWORD 'pad_test_2026';
CREATE DATABASE pad_test OWNER psd; 

CREATE TABLE actualites (id SERIAL PRIMARY KEY, titre TEXT, corps TEXT, statut TEXT);
INSERT INTO actualites (titre, corps, statut) VALUES
('Nouveau terminal', 'Le port de Dakar a inauguré un nouveau terminal à conteneurs. Les autorités ont salué cet investissement majeur pour le trafic régional et la compétitivité du port.', 'publie'),
('Brouillon', 'Texte provisoire qui ne doit pas être exporté vers le chatbot car il est encore en cours de rédaction par la communication du port.', 'brouillon'),
('Saison des croisières', 'La saison des croisières a été ouverte à Dakar avec l''accueil d''un premier paquebot. Des centaines de touristes ont visité la ville et ses environs.', 'publie');

SELECT * FROM actualites

SELECT current_database(), current_user, table_schema
FROM information_schema.tables WHERE table_name = 'actualites';