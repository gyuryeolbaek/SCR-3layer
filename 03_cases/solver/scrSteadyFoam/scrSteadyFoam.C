/*---------------------------------------------------------------------------*\
Application
    scrSteadyFoam

Description
    Steady heat and reactive species transport for a monolith SCR reactor
    modelled as an anisotropic porous medium, on a frozen incompressible flow
    field (U, phi, nut from a porousSimpleFoam result).

    Model:
      - one-equation energy model with effective conductivity in porous zones
            lambda = -2 lS + 1/( eps/(2 lS + lF) + (1-eps)/(3 lS) ),  lS(T) = a + b T
      - species NO, NH3, O2, H2O (+ NH3* for acePseudoGas), mass fractions
      - kinetics (constant/scrProperties catalyst.kinetics.model):
        chatterjeeCoverage (default): Chatterjee et al., SAE 2005-01-0965, Eqs. (14)-(17).
            NH3 surface coverage theta (0..1) stays on the catalyst; at steady state
            it follows from the site balance r_ads - r_des - r_ox - r_NO = 0 (Omega
            cancels), solved per cell by bisection. Rates per catalyst volume with
            gas concentrations C [mol/m3]; volumetric source = solidFraction * rate.
        acePseudoGas: the authors' CFD-ACE+ user-subroutine form, NH3* as a
            transported pseudo-species (rates per area x sV, stoichiometry 4).
      - reaction heat from standard formation enthalpies (dH per mol NH3 / NO)
      - wall: convection to ambient, q = h (T_amb - T_w), mixed BC

    Inputs: constant/scrProperties, 0/{U,phi,nut,T,NO,NH3,NH3s,O2,H2O}
    Monitors: postProcessing/scr/monitor.dat
\*---------------------------------------------------------------------------*/

#include "fvCFD.H"
#include "mixedFvPatchFields.H"
#include "OFstream.H"

// * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * * //

int main(int argc, char *argv[])
{
    argList::addNote("Steady SCR heat/species solver on a frozen porous flow field");

    #include "setRootCase.H"
    #include "createTime.H"
    #include "createMesh.H"
    #include "createFields.H"

    // ---------------------------------------------------------------------
    const scalar Ru = 8.314;

    // monitor file
    autoPtr<OFstream> monPtr;
    if (Pstream::master())
    {
        mkDir(runTime.globalPath()/"postProcessing"/"scr");
        monPtr.reset(new OFstream(runTime.globalPath()/"postProcessing"/"scr"/"monitor.dat"));
        monPtr() << "# iter\tetaNO_out\tXNO_out_ppm\tXNH3_out_ppm\tT_out_K\tQwall_W\tXNH3s_out_ppm"
                 << "\tres_T\tres_NO\tres_NH3tot\tres_NH3s\tres_O2\tres_H2O" << nl;
    }

    const label outletID = mesh.boundaryMesh().findPatchID(outletName);
    if (outletID < 0)
    {
        FatalErrorInFunction << "outlet patch " << outletName << " not found" << exit(FatalError);
    }

    scalar etaOld = -1;
    label stable = 0;

    Info<< "\nStarting iterations\n" << endl;

    while (runTime.loop())
    {
        Info<< "Time = " << runTime.timeName() << nl;

        // ---- reaction rates --------------------------------------------------
        const scalarField& yNO = NO.primitiveField();
        const scalarField& yNH3 = NH3.primitiveField();
        const scalarField& yS = NH3s.primitiveField();
        const scalarField& yO2 = O2.primitiveField();
        const scalarField& Tc = T.primitiveField();

        scalarField& R4I = R4.primitiveFieldRef();
        scalarField& thI = theta.primitiveFieldRef();
        const label nC = mesh.nCells();
        scalarField spNO(nC, 0), spTot(nC, 0), spS(nC, 0), suS(nC, 0),
                    suO2(nC, 0), suH2O(nC, 0), sh(nC, 0);

        forAll(Tc, celli)
        {
            if (mask[celli] < 0.5) { R4I[celli] = 0; thI[celli] = 0; continue; }

            const scalar xNH3 = max(yNH3[celli], scalar(0))*Mmix/MNH3;
            const scalar xNO  = max(yNO[celli], scalar(0))*Mmix/MNO;
            const scalar xO2  = max(yO2[celli], scalar(0))*Mmix/MO2;
            const scalar Tcell = Tc[celli];
            const scalar fO2 = Foam::pow(xO2/0.02, beta);

            if (chatterjee)
            {
                // ---- Chatterjee et al. (SAE 2005-01-0965), Eqs. (14)-(17) ----------
                //  rates per catalyst volume [mol/m3_cat/s], gas concentrations C [mol/m3]
                //  r_ads = k_ads C_NH3 (1-th)                 k_ads [1/s]
                //  r_des = k_des exp(-E_des (1 - g th)/RT) th  k_des [mol/m3/s]
                //  r_ox  = k_ox exp(-E_ox/RT) (P_O2/0.02)^b th  k_ox [mol/m3/s]
                //  r_NO  = k_NO exp(-E_NO/RT) C_NO th/(1 + K th/(1-th)) (P_O2/0.02)^b  k_NO [1/s]
                //  steady site balance (Omega cancels): r_ads - r_des - r_ox - r_NO = 0
                //  rates are per mol NH3 (ox, NO) and per mol NO (SCR)
                const scalar C = pRef/(Ru*Tcell);
                const scalar cNH3 = xNH3*C, cNO = xNO*C;
                const scalar kdes0 = A1d, kox = A2*Foam::exp(-E2/(Ru*Tcell))*fO2,
                             kno = A3*Foam::exp(-E3/(Ru*Tcell))*cNO*fO2;
                auto f = [&](scalar th)
                {
                    return A1a*cNH3*(1 - th) - kdes0*Foam::exp(-E1d*(1 - gammaDes*th)/(Ru*Tcell))*th
                         - kox*th - kno*th/(1 + KLH*th/(1 - th));
                };
                scalar lo = 0, hi = 1 - 1e-12;
                for (int it = 0; it < 80; ++it)
                {
                    const scalar m = 0.5*(lo + hi);
                    if (f(m) > 0) lo = m; else hi = m;
                }
                const scalar th = 0.5*(lo + hi);
                const scalar rox = kox*th;
                const scalar rno = kno*th/(1 + KLH*th/(1 - th));
                thI[celli] = th;
                R4I[celli] = rno;

                const scalar c = fCat*Mmix*1e-3;   // per unit mole fraction -> kg/m3/s per unit Y
                spNO[celli]  = xNO > VSMALL ? -c*rno/xNO : 0;
                // steady state: net gas NH3 consumption = r_ox + r_NO (avoids the stiff ads/des pair)
                spTot[celli] = xNH3 > VSMALL ? -c*(rox + rno)/xNH3 : 0;
                suO2[celli]  = -fCat*(0.75*rox + 0.25*rno)*MO2*1e-3;
                suH2O[celli] =  fCat*1.5*(rox + rno)*MH2O*1e-3;
                sh[celli]    =  fCat*(dH2*rox + dH3*rno);
            }
            else
            {
                // ---- CFD-ACE+ user subroutine form (NH3* transported pseudo-species) --
                //  R1: NH3 -> NH3*  R2: NH3* -> NH3  R3: 4NH3*+3O2  R4: 4NH3*+4NO+O2
                //  rates per reaction event [mol/m2/s], volumetric = sV * rate
                const scalar s = min(max(yS[celli], scalar(0))*Mmix/MNH3, scalar(1 - 1e-12));
                const scalar k1 = A1a;
                const scalar k2 = A1d*Foam::exp(-E1d/(Ru*Tcell)*(1 - gammaDes*s));
                const scalar k3 = A2*Foam::exp(-E2/(Ru*Tcell))*fO2;
                const scalar k4 = A3*Foam::exp(-E3/(Ru*Tcell))*xNO/(1 + KLH*s/(1 - s))*fO2;
                const scalar r3 = k3*s, r4 = k4*s;
                R4I[celli] = r4;
                thI[celli] = s;

                const scalar xTot = xNH3 + s;
                const scalar c = sV*Mmix*1e-3;
                spNO[celli]  = xNO > VSMALL ? -c*4*k4*s/xNO : 0;
                spTot[celli] = xTot > VSMALL ? -c*4*(r3 + r4)/xTot : 0;
                suS[celli]   =  sV*k1*xTot*(1 - s)*MNH3*1e-3;
                spS[celli]   = -c*(k1*(1 - s) + k2 + 4*k3 + 4*k4);
                suO2[celli]  = -sV*(3*r3 + r4)*MO2*1e-3;
                suH2O[celli] =  sV*6*(r3 + r4)*MH2O*1e-3;
                sh[celli]    =  sV*4*(dH2*r3 + dH3*r4);
            }
        }

        auto internal = [&](const word& n, const dimensionSet& d, const scalarField& f)
        {
            return volScalarField::Internal(IOobject(n, runTime.timeName(), mesh), mesh, d, f);
        };
        const volScalarField::Internal SpNO(internal("SpNO", dimDensity/dimTime, spNO));
        const volScalarField::Internal SpTot(internal("SpTot", dimDensity/dimTime, spTot));
        const volScalarField::Internal SpS(internal("SpS", dimDensity/dimTime, spS));
        const volScalarField::Internal SuS(internal("SuS", dimDensity/dimTime, suS));
        const volScalarField::Internal SuO2(internal("SuO2", dimDensity/dimTime, suO2));
        const volScalarField::Internal SuH2O(internal("SuH2O", dimDensity/dimTime, suH2O));
        const volScalarField::Internal Sh(internal("Sh", dimPower/dimVolume, sh));

        // ---- species ---------------------------------------------------------
        const volScalarField rhoDeff
        (
            "rhoDeff", rho*(Dm*(mask*porosity + (1 - mask)) + nut/Sct)
        );

        scalar resNO, resTot, resS, resO2, resH2O;
        {
            fvScalarMatrix eq
            (
                fvm::div(rhoPhi, NO, "div(phi,Yi)") - fvm::laplacian(rhoDeff, NO) == fvm::Sp(SpNO, NO)
            );
            eq.relax();
            resNO = eq.solve().initialResidual();
        }
        {
            // total ammonia (NH3 + NH3*): same transport, only slow consumption
            NH3tot = NH3 + NH3s;
            fvScalarMatrix eq
            (
                fvm::div(rhoPhi, NH3tot, "div(phi,Yi)") - fvm::laplacian(rhoDeff, NH3tot)
             == fvm::Sp(SpTot, NH3tot)
            );
            eq.relax();
            resTot = eq.solve().initialResidual();
        }
        resS = 0;
        if (!chatterjee)
        {
            fvScalarMatrix eq
            (
                fvm::div(rhoPhi, NH3s, "div(phi,Yi)") - fvm::laplacian(rhoDeff, NH3s)
             == fvm::Sp(SpS, NH3s) + SuS
            );
            eq.relax();
            resS = eq.solve().initialResidual();
        }
        {
            fvScalarMatrix eq
            (
                fvm::div(rhoPhi, O2, "div(phi,Yi)") - fvm::laplacian(rhoDeff, O2) == SuO2
            );
            eq.relax();
            resO2 = eq.solve().initialResidual();
        }
        {
            fvScalarMatrix eq
            (
                fvm::div(rhoPhi, H2O, "div(phi,Yi)") - fvm::laplacian(rhoDeff, H2O) == SuH2O
            );
            eq.relax();
            resH2O = eq.solve().initialResidual();
        }
        forAll(NO, i)
        {
            NO[i] = max(NO[i], scalar(0));
            NH3s[i] = min(max(NH3s[i], scalar(0)), max(NH3tot[i], scalar(0)));
            NH3[i] = max(NH3tot[i] - NH3s[i], scalar(0));
        }
        NO.correctBoundaryConditions(); NH3s.correctBoundaryConditions(); NH3.correctBoundaryConditions();

        // ---- energy ----------------------------------------------------------
        #include "updateKappa.H"

        scalar resT;
        {
            fvScalarMatrix eq
            (
                fvm::div(rhoPhi, T, "div(phi,T)") - fvm::laplacian(kappaEff/cp, T) == Sh/cp
            );
            eq.relax();
            resT = eq.solve().initialResidual();
        }

        // ---- monitors --------------------------------------------------------
        const scalarField& phiOut = phi.boundaryField()[outletID];
        const scalar sumPhi = gSum(phiOut);
        const scalar xNOout  = gSum(phiOut*NO.boundaryField()[outletID])/sumPhi*Mmix/MNO;
        const scalar xNH3out = gSum(phiOut*NH3.boundaryField()[outletID])/sumPhi*Mmix/MNH3;
        const scalar xSout   = gSum(phiOut*NH3s.boundaryField()[outletID])/sumPhi*Mmix/MNH3;
        const scalar Tout    = gSum(phiOut*T.boundaryField()[outletID])/sumPhi;
        const scalar eta     = 1 - xNOout/XNOin;

        scalar Qwall = 0;
        for (const label patchi : wallIDs)
        {
            Qwall += gSum
            (
                kappaEff.boundaryField()[patchi]*T.boundaryField()[patchi].snGrad()
               *mesh.magSf().boundaryField()[patchi]
            );
        }

        Info<< "    eta_NO(outlet) = " << eta*100 << " %, X_NH3,out = " << xNH3out*1e6
            << " ppm, X_NH3*,out = " << xSout*1e6 << " ppm, T_out = " << Tout << " K, Q_wall = " << Qwall << " W" << endl;

        if (Pstream::master())
        {
            monPtr() << runTime.timeName() << tab << eta << tab << xNOout*1e6 << tab
                     << xNH3out*1e6 << tab << Tout << tab << Qwall << tab << xSout*1e6 << tab
                     << resT << tab << resNO << tab << resTot << tab << resS << tab
                     << resO2 << tab << resH2O << endl;
        }

        // ---- output fields ---------------------------------------------------
        XNO_ppm = NO*(Mmix/MNO*1e6);
        XNH3_ppm = NH3*(Mmix/MNH3*1e6);
        XNH3s_ppm = NH3s*(Mmix/MNH3*1e6);

        // ---- convergence -----------------------------------------------------
        const scalar resMax = max(max(max(resT, resNO), max(resTot, resS)), max(resO2, resH2O));
        if (etaOld >= 0 && mag(eta - etaOld) < etaTol && resMax < resTol) ++stable; else stable = 0;
        etaOld = eta;

        if (stable >= nStable)
        {
            Info<< "\nscrSteadyFoam: converged (residuals < " << resTol
                << ", |d eta| < " << etaTol << " for " << nStable << " iterations)" << nl << endl;
            runTime.writeAndEnd();
            break;
        }

        runTime.write();
        runTime.printExecutionTime(Info);
    }

    Info<< "End\n" << endl;
    return 0;
}

// ************************************************************************* //
