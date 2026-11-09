-- Run once in Supabase SQL Editor. Only portfolio_ objects are created.
-- Browser API keys cannot read or write portfolio tables.
BEGIN;
CREATE TABLE IF NOT EXISTS public.portfolio_projects (
  id text NOT NULL PRIMARY KEY,
  title text NOT NULL,
  created_on text NOT NULL,
  category text NOT NULL,
  work_type text NOT NULL,
  status text NOT NULL,
  description text NOT NULL,
  problem text NOT NULL,
  outcome text NOT NULL,
  impact text NOT NULL,
  audience text NOT NULL,
  collaborators text NOT NULL,
  tools text NOT NULL,
  tags text NOT NULL,
  hours double precision NOT NULL CHECK (hours >= 0),
  updated_at text NOT NULL
);

ALTER TABLE public.portfolio_projects ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.portfolio_projects FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.portfolio_projects TO service_role;

CREATE TABLE IF NOT EXISTS public.portfolio_deliverables (
  project_id text NOT NULL REFERENCES public.portfolio_projects(id) ON DELETE CASCADE,
  kind text NOT NULL,
  PRIMARY KEY (project_id,kind)
);

ALTER TABLE public.portfolio_deliverables ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.portfolio_deliverables FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.portfolio_deliverables TO service_role;

CREATE TABLE IF NOT EXISTS public.portfolio_links (
  id text NOT NULL PRIMARY KEY,
  project_id text NOT NULL REFERENCES public.portfolio_projects(id) ON DELETE CASCADE,
  label text NOT NULL,
  url text NOT NULL
);

ALTER TABLE public.portfolio_links ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.portfolio_links FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.portfolio_links TO service_role;

CREATE TABLE IF NOT EXISTS public.portfolio_files (
  id text NOT NULL PRIMARY KEY,
  project_id text NOT NULL REFERENCES public.portfolio_projects(id) ON DELETE CASCADE,
  original_name text NOT NULL,
  saved_name text NOT NULL,
  size_bytes bigint NOT NULL CHECK (size_bytes >= 0),
  storage_path text NOT NULL UNIQUE
);

ALTER TABLE public.portfolio_files ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.portfolio_files FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.portfolio_files TO service_role;

CREATE TABLE IF NOT EXISTS public.portfolio_activities (
  id text NOT NULL PRIMARY KEY,
  project_id text NOT NULL REFERENCES public.portfolio_projects(id) ON DELETE CASCADE,
  activity_on text NOT NULL,
  note text NOT NULL,
  hours double precision NOT NULL CHECK (hours >= 0)
);

ALTER TABLE public.portfolio_activities ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.portfolio_activities FROM anon, authenticated, PUBLIC;
GRANT ALL ON public.portfolio_activities TO service_role;
CREATE OR REPLACE FUNCTION public.portfolio_save_project(p_id text,p_data jsonb,p_kinds jsonb,p_create boolean)
RETURNS text LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
DECLARE r public.portfolio_projects;
BEGIN
  LOCK TABLE public.portfolio_projects IN SHARE ROW EXCLUSIVE MODE;
  r := jsonb_populate_record(NULL::public.portfolio_projects,
        p_data || jsonb_build_object('id',p_id,'updated_at',to_char(clock_timestamp(),'YYYY-MM-DD"T"HH24:MI:SS.US')));
  IF p_create THEN
    INSERT INTO public.portfolio_projects SELECT (r).*;
  ELSE
    IF NOT EXISTS (SELECT 1 FROM public.portfolio_projects WHERE id=p_id) THEN
      RAISE EXCEPTION 'Project not found';
    END IF;
    UPDATE public.portfolio_projects SET title=r.title,created_on=r.created_on,category=r.category,work_type=r.work_type,status=r.status,description=r.description,problem=r.problem,outcome=r.outcome,impact=r.impact,audience=r.audience,collaborators=r.collaborators,tools=r.tools,tags=r.tags,hours=r.hours,updated_at=r.updated_at WHERE id=p_id;
  END IF;
  DELETE FROM public.portfolio_deliverables WHERE project_id=p_id;
  INSERT INTO public.portfolio_deliverables(project_id,kind)
    SELECT p_id,value FROM jsonb_array_elements_text(p_kinds) ON CONFLICT DO NOTHING;
  RETURN p_id;
END; $$;

CREATE OR REPLACE FUNCTION public.portfolio_import(p_tables jsonb)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path=public AS $$
BEGIN
  LOCK TABLE public.portfolio_projects IN SHARE ROW EXCLUSIVE MODE;
  IF EXISTS (SELECT 1 FROM public.portfolio_projects) THEN
    RAISE EXCEPTION 'Import requires an empty portfolio';
  END IF;
  INSERT INTO public.portfolio_projects SELECT * FROM jsonb_populate_recordset(NULL::public.portfolio_projects,p_tables->'projects');
  INSERT INTO public.portfolio_deliverables SELECT * FROM jsonb_populate_recordset(NULL::public.portfolio_deliverables,p_tables->'deliverables');
  INSERT INTO public.portfolio_links SELECT * FROM jsonb_populate_recordset(NULL::public.portfolio_links,p_tables->'links');
  INSERT INTO public.portfolio_files SELECT * FROM jsonb_populate_recordset(NULL::public.portfolio_files,p_tables->'files');
  INSERT INTO public.portfolio_activities SELECT * FROM jsonb_populate_recordset(NULL::public.portfolio_activities,p_tables->'activities');
END; $$;

CREATE OR REPLACE FUNCTION public.portfolio_snapshot()
RETURNS jsonb LANGUAGE sql STABLE SECURITY INVOKER SET search_path=public AS $$
 SELECT jsonb_build_object(
  'projects',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (SELECT * FROM public.portfolio_projects ORDER BY id) r),'[]'::jsonb),
  'deliverables',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (SELECT * FROM public.portfolio_deliverables ORDER BY project_id,kind) r),'[]'::jsonb),
  'links',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (SELECT * FROM public.portfolio_links ORDER BY id) r),'[]'::jsonb),
  'files',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (SELECT * FROM public.portfolio_files ORDER BY id) r),'[]'::jsonb),
  'activities',COALESCE((SELECT jsonb_agg(to_jsonb(r)) FROM (SELECT * FROM public.portfolio_activities ORDER BY id) r),'[]'::jsonb)
 );
$$;
REVOKE ALL ON FUNCTION public.portfolio_save_project(text,jsonb,jsonb,boolean) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.portfolio_save_project(text,jsonb,jsonb,boolean) TO service_role;
REVOKE ALL ON FUNCTION public.portfolio_import(jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.portfolio_import(jsonb) TO service_role;
REVOKE ALL ON FUNCTION public.portfolio_snapshot() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.portfolio_snapshot() TO service_role;
NOTIFY pgrst, 'reload schema';
COMMIT;
