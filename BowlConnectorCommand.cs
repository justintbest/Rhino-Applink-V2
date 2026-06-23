using System;
using Rhino;
using Rhino.Commands;

namespace BowlConnector
{
    public class BowlConnectorCommand : Command
    {
        public override string EnglishName => "BowlConnector";

        protected override Result RunCommand(RhinoDoc doc, RunMode mode)
        {
            const string script =
                "import glob,os,sys; p=sorted(glob.glob(os.path.expandvars(r'%APPDATA%\\McNeel\\Rhinoceros\\packages\\8.0\\bowl-connector\\*\\BowlConnector\\rhino_api_sender.py'))); exec(open(p[-1]).read()) if p else None";

            bool ok = RhinoApp.RunScript("_NoEcho _RunPythonScript \"" + script + "\"", false);
            return ok ? Result.Success : Result.Failure;
        }
    }
}
